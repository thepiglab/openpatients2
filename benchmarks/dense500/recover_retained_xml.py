"""Recover small retained-source XML snapshots, verifying original hashes.

No annotation is altered. New source segments are appended only after exact
comparison with the frozen packet, and affected annotations require re-review.
"""
import asyncio,hashlib,json
from pathlib import Path
from acquire import BoundedClient,ROOT,save
from openpatients2.articles import parse_article

async def main():
    old=ROOT.parent/'dense50';(old/'raw-xml').mkdir(exist_ok=True)
    client=BoundedClient(); network=ROOT/'acquisition/network.json'
    if network.exists():client.stats.update(json.loads(network.read_text()))
    changes=[]
    try:
      for path in sorted((old/'sources').glob('*.json')):
        s=json.loads(path.read_text());aid=s['article_id'];raw=old/'raw-xml'/f'{aid}.xml'
        if raw.exists():continue
        xml,_=await client._text(s['retrieval']['xml']['url'],'xml')
        assert hashlib.sha256(xml.encode()).hexdigest()==s['xml_sha256'],aid
        metadata_text,_=await client._text(s['retrieval']['metadata']['url'],'metadata')
        metadata=json.loads(metadata_text)
        if metadata.get('pmcid')!=s['pmcid']:raise ValueError('PMCID mismatch')
        revised=parse_article(xml,metadata,s['retrieval']);raw.write_text(xml)
        # Retained packet may omit parser metadata but its evidence is immutable.
        old_segments=s['segments'];old_count=len(old_segments);new_segments=revised['segments']
        same_prefix=len(new_segments)>=len(old_segments) and all(a['segment_id']==b['segment_id'] and a['text']==b['text'] and a['heading']==b['heading'] for a,b in zip(old_segments,new_segments))
        if not same_prefix:
          save(old/'acquisition'/f'{aid}.recovered.json',revised)
          changes.append({'article_id':aid,'status':'manual_nonappend_review_needed'})
          continue
        s['metadata']=metadata
        if len(new_segments)>len(old_segments):
          s['segments']+=new_segments[len(old_segments):]
          s['text']='\n\n'.join(f'[{x["segment_id"]}] {x["heading"] or "Article"}\n{x["text"]}' for x in s['segments'])
          s['text_sha256']=hashlib.sha256(s['text'].encode()).hexdigest()
          s['parser_version']='1.5'
          (old/'source-text'/f'{aid}.txt').write_text(s['title']+'\n\n'+s['text'])
        save(path,s)
        changes.append({'article_id':aid,'status':'verified','added_segments':len(new_segments)-old_count,'text_sha256':s['text_sha256']})
        save(ROOT/'acquisition/retained-xml-recovery.json',changes);save(network,dict(client.stats))
        print(aid,changes[-1],flush=True)
    finally:
      save(ROOT/'acquisition/retained-xml-recovery.json',changes);save(network,dict(client.stats));await client.close()

if __name__=='__main__':asyncio.run(main())
