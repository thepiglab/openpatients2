"""Append formerly omitted floating-table evidence without rewriting old IDs."""
import json
from pathlib import Path
from openpatients2.articles import parse_article

ROOT=Path(__file__).resolve().parent

def main():
    changes=[]
    for path in sorted((ROOT/'sources').glob('*.json')):
        article=json.loads(path.read_text())
        raw=ROOT/'raw-xml'/f'{article["article_id"]}.xml'
        if not raw.exists(): continue
        revised=parse_article(raw.read_text(),article['metadata'],article['retrieval'])
        if revised['segments']==article['segments']: continue
        old=article['segments']
        if revised['segments'][:len(old)]!=old:
            raise ValueError(f'{path.name}: non-append source change requires manual review')
        revised['status']=article['status']
        added=revised['segments'][len(old):]
        change={'article_id':article['article_id'],'old_text_sha256':article['text_sha256'],
            'new_text_sha256':revised['text_sha256'],'added_segment_ids':[s['segment_id'] for s in added],
            'added_table_ids':sorted({s.get('table_id') for s in added}),
            'draft_needs_update':(ROOT/'drafts'/path.name).exists()}
        temp=path.with_suffix('.json.tmp');temp.write_text(json.dumps(revised,ensure_ascii=False,indent=2)+'\n');temp.replace(path)
        (ROOT/'source-text'/f'{article["article_id"]}.txt').write_text(revised['title']+'\n\n'+revised['text'])
        with (ROOT/'acquisition/source-repairs.jsonl').open('a') as stream:stream.write(json.dumps(change)+'\n')
        changes.append(change)
    print(json.dumps(changes,indent=2))

if __name__=='__main__':main()
