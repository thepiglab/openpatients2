"""op2 dense video_frames: source-listed videos, capped cache download, ffmpeg frame sampling, cleanup (offline)."""
import hashlib
import json
import shutil
import subprocess

import httpx
import pytest

from openpatients2 import dense_store as store
from openpatients2.dense import TOOLS, video_frames as vf

HOST = 'https://pmc-oa-opendata.s3.amazonaws.com/PMCX.1/'


def _source(md5=None):
    return {'article_id': 'PMCX.1', 'text_sha256': 'abc',
            'figures': [{'figure_key': 'VID1', 'image_urls': [],
                         'media': [{'url': HOST + 'case-i01.mp4', 'filename': 'case-i01.mp4', 'kind': 'video', 'md5': md5}]},
                        {'figure_key': 'FIG1', 'image_urls': [HOST + 'a.jpg'], 'media': [{'url': HOST + 'a.jpg'}]}],
            'supplements': [{'supplement_id': 'SM1', 'assets': [{'url': HOST + 'Video_1.mp4', 'kind': 'video'}]}],
            'unassigned_media': [{'url': HOST + 'case-i01-pmcvs_normal.mp4', 'filename': 'case-i01-pmcvs_normal.mp4',
                                  'kind': 'video', 'availability': 'listed_in_pmc_metadata', 'http_verified': False},
                                 {'url': HOST + 'Video_1.mp4', 'kind': 'video'},
                                 {'url': HOST + 'loose.mp4', 'kind': 'video'}]}


def test_registered_and_candidates_link_transcodes_to_their_owner():
    assert 'video_frames' in TOOLS
    rows = {r['filename']: r for r in vf.candidates(_source())}
    assert set(rows) == {'case-i01.mp4', 'Video_1.mp4', 'case-i01-pmcvs_normal.mp4', 'loose.mp4'}
    assert rows['case-i01-pmcvs_normal.mp4']['figure_id'] == 'VID1'
    assert rows['Video_1.mp4']['supplement_id'] == 'SM1' and rows['Video_1.mp4']['origins'] == ['supplement_asset', 'unassigned_media']
    assert rows['loose.mp4']['figure_id'] is None and rows['loose.mp4']['supplement_id'] is None
    with pytest.raises(ValueError, match='Several videos'):
        vf._select(_source(), None)


def test_timestamps_cover_the_whole_duration():
    assert vf.timestamps(4.1, 1, 20) == [0.0, 1.0, 2.0, 3.0, 4.0]
    spread = vf.timestamps(100, 1, 5)
    assert len(spread) == 5 and spread[0] == 0 and spread[-1] > 99
    assert vf.timestamps(None, 2, 10) == [0.0]
    with pytest.raises(ValueError):
        vf.timestamps(10, 0, 10)


def test_download_rejects_undeclared_hosts_and_caps(tmp_path):
    row = {'url': 'https://example.com/v.mp4', 'filename': 'v.mp4', 'md5': None}
    with pytest.raises(ValueError, match='PMC/NCBI'):
        vf.download(row, 'PMCX.1', cache=tmp_path)
    client = httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(200, headers={'content-length': '999'})))
    row = {'url': HOST + 'v.mp4', 'filename': 'v.mp4', 'md5': None}
    with pytest.raises(ValueError, match='per-file cap'):
        vf.download(row, 'PMCX.1', cache=tmp_path, client=client, max_file=10)
    with pytest.raises(vf.CacheFull):
        vf.download(row, 'PMCX.1', cache=tmp_path, client=client, max_cache=10)


@pytest.mark.skipif(not (shutil.which('ffmpeg') and shutil.which('ffprobe')), reason='system ffmpeg not installed')
def test_download_sample_and_cleanup_end_to_end(tmp_path):
    clip = tmp_path / 'clip.mp4'
    subprocess.run(['ffmpeg', '-nostdin', '-v', 'error', '-f', 'lavfi', '-i', 'testsrc=duration=3:size=64x48:rate=10',
                    '-pix_fmt', 'yuv420p', str(clip)], check=True)
    data = clip.read_bytes()
    source = _source(md5=hashlib.md5(data).hexdigest())
    root = tmp_path / 'root'
    out = store.path(root, 'sources', 'PMCX.1'); out.parent.mkdir(parents=True); out.write_text(json.dumps(source))

    def handler(request):
        assert request.url.host == 'pmc-oa-opendata.s3.amazonaws.com'
        headers = {'content-length': str(len(data))}
        return httpx.Response(200, headers=headers, content=b'' if request.method == 'HEAD' else data)

    cache = tmp_path / 'cache'; cache.mkdir()
    (cache / 'PMCX.1').mkdir(); (cache / 'PMCX.1' / 'figure.image').write_bytes(b'keep')
    result = vf.run(root, 'PMCX.1', video='case-i01.mp4', every=1, max_frames=10, cache=cache,
                    client=httpx.Client(transport=httpx.MockTransport(handler)))
    assert result['video']['figure_id'] == 'VID1' and result['media_probe']['width'] == 64
    assert [f['t'] for f in result['frames']] == [0.0, 1.0, 2.0]
    template = result['video_inspection_template']
    assert template['format'] == 'dense-video-frame-inspection/1' and template['asset']['sha256'] == hashlib.sha256(data).hexdigest()
    assert all(f['sha256'] and (cache / 'PMCX.1').joinpath(f['path'].rsplit('/', 1)[-1]).exists()
               for f in template['sampling']['frames'])
    assert result['record_as'] == 'articles/PMCX.1/video_inspection.VID1.<role>.json'
    gone = vf.cleanup('PMCX.1', cache=cache)
    assert gone['removed_files'] == 4 and [p.name for p in (cache / 'PMCX.1').iterdir()] == ['figure.image']
