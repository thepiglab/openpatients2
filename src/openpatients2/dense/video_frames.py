"""Download one article's source-listed PMC video and sample frames for a reviewer to view.

  op2 dense video_frames ROOT --article ID --list                     # candidate videos, no network
  op2 dense video_frames ROOT --article ID [--video FILENAME] [--every SECONDS] [--max-frames N]
  op2 dense video_frames ROOT --article ID [--video FILENAME] --cleanup

Videos are collected from the source record: figure media (e.g. VID1 -> *.mp4), supplement assets and
`unassigned_media` entries with kind video (PMC lists a transcoded `*-pmcvs_normal.mp4` next to the original;
it is linked to the figure or supplement whose file shares its stem). Only declared HTTPS PMC/NCBI hosts are
fetched. The MP4 goes into the shared, gitignored figure cache (`data/.dense-figure-cache/<ID>/`, the cache
`figure_assets` uses, under its lock and its 1 GB cap; per-file cap as in `supplement_assets`), is checked
against the source MD5 and probed with ffprobe, and frames are sampled with the system `ffmpeg` at fixed
timestamps across the whole duration. Frames are PNGs in the same cache folder; their paths, timestamps and
hashes are printed together with a `video_inspection_template` for the record a reviewer writes after viewing
them (`articles/<ID>/video_inspection.<FID>.<role>.json`; see benchmarks/dense500/claude_prompts). A video
supplement is recorded in the supplement manifest instead (basis `manifest_video_frames_sampled`).

Downloading and sampling are not inspection. This tool never writes into articles/. `--cleanup` removes only
this tool's files (video-*) for the article; figure images are left alone.
"""
import argparse
import fcntl
import hashlib
import json
import math
import re
import shutil
import subprocess
from pathlib import Path
from urllib.parse import urlparse

import httpx

from openpatients2 import dense_store as store
from openpatients2.dense import DENSE500
from openpatients2.dense.figure_assets import CACHE, MAX_CACHE_BYTES
from openpatients2.dense.supplement_assets import ALLOWED_HOSTS, MAX_FILE_BYTES, CacheFull, _probe, _stream, cache_usage
from openpatients2.supplement_peek import VIDEO_EXTENSIONS

INSPECTION_FORMAT = 'dense-video-frame-inspection/1'
DEFAULT_EVERY = 2.0
DEFAULT_MAX_FRAMES = 60
HARD_MAX_FRAMES = 400


def _filename(entry):
    return entry.get('filename') or (entry.get('url') or '').split('?')[0].rsplit('/', 1)[-1]


def _is_video(entry):
    name = _filename(entry).lower()
    return entry.get('kind') == 'video' or any(name.endswith(ext) for ext in VIDEO_EXTENSIONS)


def _stem(name):
    stem = name.rsplit('.', 1)[0]
    return stem[:-len('-pmcvs_normal')] if stem.endswith('-pmcvs_normal') else stem


def candidates(source):
    """Every video the source record lists, deduplicated by URL, with its owning figure/supplement if any."""
    rows = {}

    def add(entry, origin, figure_id=None, supplement_id=None):
        url = entry.get('url')
        if not url or not _is_video(entry):
            return
        row = rows.setdefault(url, {'filename': _filename(entry), 'url': url, 'md5': entry.get('md5'),
                                    'origins': [], 'figure_id': None, 'supplement_id': None,
                                    'availability': entry.get('availability'),
                                    'http_verified': entry.get('http_verified')})
        row['origins'].append(origin)
        row['figure_id'] = row['figure_id'] or figure_id
        row['supplement_id'] = row['supplement_id'] or supplement_id

    for figure in source.get('figures', []):
        for media in figure.get('media', []):
            add(media, 'figure_media', figure_id=figure['figure_key'])
    for supplement in source.get('supplements', []):
        for asset in supplement.get('assets', []):
            add(asset, 'supplement_asset', supplement_id=supplement['supplement_id'])
    for media in source.get('unassigned_media') or []:
        add(media, 'unassigned_media')
    owners = {_stem(r['filename']): r for r in rows.values() if r['figure_id'] or r['supplement_id']}
    for row in rows.values():
        owner = owners.get(_stem(row['filename']))
        if owner and owner is not row and not (row['figure_id'] or row['supplement_id']):
            row.update(figure_id=owner['figure_id'], supplement_id=owner['supplement_id'],
                       linked_by='same_stem_as_' + owner['filename'])
    return list(rows.values())


def _select(source, video):
    rows = candidates(source)
    if not rows:
        raise ValueError('The source record lists no video for this article')
    if video:
        chosen = [r for r in rows if r['filename'] == video]
        if not chosen:
            raise ValueError(f'No listed video named {video!r}; listed: {[r["filename"] for r in rows]}')
        return chosen[0]
    if len(rows) > 1:
        raise ValueError(f'Several videos are listed; pass --video one of {[r["filename"] for r in rows]}')
    return rows[0]


def _base(cache, aid, url):
    return Path(cache) / store._check(aid, 'article id') / ('video-' + hashlib.sha256(url.encode()).hexdigest()[:16])


def _tool(name):
    path = shutil.which(name)
    if not path:
        raise RuntimeError(f'System `{name}` not found on PATH; install ffmpeg (which provides ffmpeg and ffprobe)')
    return path


def probe(path):
    raw = subprocess.run([_tool('ffprobe'), '-v', 'error', '-print_format', 'json', '-show_format', '-show_streams',
                          str(path)], capture_output=True, text=True, timeout=120)
    if raw.returncode:
        raise ValueError('ffprobe could not read the file: ' + raw.stderr.strip()[:300])
    info = json.loads(raw.stdout)
    video = next((s for s in info.get('streams', []) if s.get('codec_type') == 'video'), None)
    if video is None:
        raise ValueError('File has no video stream')
    audio = next((s for s in info.get('streams', []) if s.get('codec_type') == 'audio'), {})
    duration = info.get('format', {}).get('duration') or video.get('duration')
    return {'duration_seconds': float(duration) if duration else None, 'width': video.get('width'),
            'height': video.get('height'), 'video_codec': video.get('codec_name'),
            'average_frame_rate': video.get('avg_frame_rate'), 'audio_codec': audio.get('codec_name')}


def timestamps(duration, every, max_frames):
    """Fixed-interval timestamps from 0 across the whole duration; spread evenly when the cap would truncate."""
    if every <= 0 or not 1 <= max_frames <= HARD_MAX_FRAMES:
        raise ValueError(f'--every must be positive and --max-frames within 1..{HARD_MAX_FRAMES}')
    if not duration or duration <= 0:
        return [0.0]
    last = max(0.0, duration - 0.05)   # the final instant often has no decodable frame
    count = int(math.floor(last / every)) + 1
    if count <= max_frames:
        return [round(i * every, 3) for i in range(count)]
    if max_frames == 1:
        return [0.0]
    return [round(i * last / (max_frames - 1), 3) for i in range(max_frames)]


def download(row, aid, *, client=None, cache=None, max_cache=MAX_CACHE_BYTES, max_file=MAX_FILE_BYTES):
    """Fetch the declared MP4 into the shared figure cache under its lock and cap; verify MD5. Returns the path."""
    cache = Path(cache or CACHE)
    parsed = urlparse(row['url'])
    if parsed.scheme != 'https' or parsed.hostname not in ALLOWED_HOSTS:
        raise ValueError('Only declared HTTPS PMC/NCBI hosts are accepted: ' + str(parsed.hostname))
    ext = '.' + row['filename'].rsplit('.', 1)[-1].lower() if '.' in row['filename'] else '.mp4'
    path = _base(cache, aid, row['url']).with_suffix(ext if re.fullmatch(r'\.[a-z0-9]{1,5}', ext) else '.mp4')
    path.parent.mkdir(parents=True, exist_ok=True)
    owns = client is None
    client = client or httpx.Client(timeout=300, follow_redirects=False)
    try:
        with (cache / '.lock').open('a') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            if not path.exists():
                size, _ = _probe(client, row['url'])
                if size is None:
                    raise ValueError('Server did not report a size; refusing an unbounded download')
                if size > max_file:
                    raise ValueError(f'Video is {size} bytes, over the per-file cap of {max_file}')
                if cache_usage(cache) + size > max_cache:
                    raise CacheFull('Shared 1 GB figure cache cap reached; clean up reviewed articles first')
                written, truncated, _ = _stream(client, row['url'], path, size)
                if truncated or written != size:
                    path.unlink(missing_ok=True)
                    raise ValueError('Downloaded byte count differs from declared size')
            data = path.read_bytes()
            if row.get('md5') and hashlib.md5(data).hexdigest() != row['md5']:
                path.unlink()
                raise ValueError('Source manifest MD5 mismatch; cached copy removed')
    finally:
        if owns:
            client.close()
    return path


def sample(video, times, *, cache=None, max_cache=MAX_CACHE_BYTES):
    """One PNG per timestamp (accurate seek, full resolution) next to the video; returns frame rows."""
    cache = Path(cache or CACHE)
    ffmpeg = _tool('ffmpeg')
    frames = []
    with (cache / '.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        for index, t in enumerate(times, 1):
            out = video.with_name(f'{video.stem}-f{index:03d}-{t:09.3f}s.png')
            if not out.exists():
                if cache_usage(cache) >= max_cache:
                    raise CacheFull('Shared 1 GB figure cache cap reached while sampling; clean up first')
                done = subprocess.run([ffmpeg, '-nostdin', '-v', 'error', '-y', '-ss', f'{t:.3f}', '-i', str(video),
                                       '-frames:v', '1', str(out)], capture_output=True, text=True, timeout=300)
                if done.returncode or not out.exists():
                    out.unlink(missing_ok=True)
                    raise ValueError(f'ffmpeg failed at {t}s: ' + done.stderr.strip()[:300])
            data = out.read_bytes()
            frames.append({'index': index, 'requested_timestamp_seconds': t, 'path': str(out.resolve()),
                           'sha256': hashlib.sha256(data).hexdigest(), 'bytes': len(data)})
    return frames


def run(root, aid, *, video=None, every=DEFAULT_EVERY, max_frames=DEFAULT_MAX_FRAMES, client=None, cache=None):
    source = json.loads(store.path(root, 'sources', aid).read_text())
    row = _select(source, video)
    path = download(row, aid, client=client, cache=cache)
    data = path.read_bytes()
    media = probe(path)
    times = timestamps(media['duration_seconds'], every, max_frames)
    frames = sample(path, times, cache=cache)
    asset = {'url': row['url'], 'md5': row['md5'], 'sha256': hashlib.sha256(data).hexdigest(), 'bytes': len(data),
             'path': str(path.resolve())}
    interval = (times[1] - times[0]) if len(times) > 1 else None
    template = {
        'format': INSPECTION_FORMAT, 'article_id': aid, 'source_text_sha256': source['text_sha256'],
        'figure_id': row['figure_id'], 'status': 'partial_frame_sampling_inspected', 'asset': asset,
        'media_probe': media, 'continuous_motion_inspected': False, 'audio_inspected': False,
        'sampling': {'method': f'op2 dense video_frames: ffmpeg accurate seek, one full-resolution PNG per requested '
                               f'timestamp, {len(times)} frames from 0 to {times[-1]}s'
                               + (f' every {interval:g}s' if interval else ''),
                     'sample_count': len(frames), 'frames': frames},
        'pixel_observations': 'FILL after viewing every frame: [{sample_interval_seconds: [a, b], description}]',
        'author_context': 'FILL: {patient_ids, subject, evidence: [{segment_id, quote}], rationale}',
        'limitations': ['Sampled frames only; intervening frames and continuous motion were not viewed.',
                        'Audio not inspected.' if media.get('audio_codec') else 'No audio stream.'],
    }
    return {'article_id': aid, 'video': {k: row.get(k) for k in ('filename', 'url', 'figure_id', 'supplement_id',
                                                                 'origins', 'linked_by') if row.get(k)},
            'asset': asset, 'media_probe': media,
            'frames': [{'t': f['requested_timestamp_seconds'], 'path': f['path']} for f in frames],
            'cache_bytes_after': cache_usage(Path(cache or CACHE)), 'cache_limit_bytes': MAX_CACHE_BYTES,
            'download_is_not_inspection': True,
            'record_as': (f'supplement manifest row for {row["supplement_id"]} (manifest_video_frames_sampled)'
                          if row['supplement_id'] and not row['figure_id'] else
                          f'articles/{aid}/video_inspection.{row["figure_id"] or "<FID>"}.<role>.json'),
            'video_inspection_template': template}


def cleanup(aid, *, video=None, root=None, cache=None):
    """Remove this tool's cached video and frames (video-*) for the article, or for one --video."""
    cache = Path(cache or CACHE)
    folder = cache / store._check(aid, 'article id')
    if video:
        source = json.loads(store.path(root, 'sources', aid).read_text())
        prefixes = [_base(cache, aid, _select(source, video)['url']).name]
    else:
        prefixes = ['video-']
    freed, removed = 0, 0
    if folder.exists():
        with (cache / '.lock').open('a') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            for path in folder.iterdir():
                if path.is_file() and any(path.name.startswith(p) for p in prefixes):
                    freed += path.stat().st_size; removed += 1
                    path.unlink()
            if not any(folder.iterdir()):
                folder.rmdir()
    return {'article_id': aid, 'removed_files': removed, 'freed_bytes': freed,
            'cache_bytes_after': cache_usage(cache)}


if __name__ == '__main__':
    p = argparse.ArgumentParser(prog='op2 dense video_frames', description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument('root', type=Path, nargs='?', default=DENSE500)
    p.add_argument('--article', required=True)
    p.add_argument('--video', help='Filename of one listed video (required when several are listed)')
    p.add_argument('--every', type=float, default=DEFAULT_EVERY, help='Seconds between sampled frames')
    p.add_argument('--max-frames', type=int, default=DEFAULT_MAX_FRAMES,
                   help='Frame cap; when exceeded, frames are spread evenly across the whole duration')
    mode = p.add_mutually_exclusive_group()
    mode.add_argument('--list', action='store_true', help='List the source-listed videos (no network)')
    mode.add_argument('--cleanup', action='store_true', help="Remove this tool's cached video/frames for the article")
    args = p.parse_args()
    try:
        if args.list:
            source = json.loads(store.path(args.root, 'sources', args.article).read_text())
            result = {'article_id': args.article, 'videos': candidates(source)}
        elif args.cleanup:
            result = cleanup(args.article, video=args.video, root=args.root)
        else:
            result = run(args.root, args.article, video=args.video, every=args.every, max_frames=args.max_frames)
    except (ValueError, RuntimeError, CacheFull, httpx.HTTPError, OSError, subprocess.TimeoutExpired) as exc:
        raise SystemExit(f'video_frames: {type(exc).__name__}: {exc}')
    print(json.dumps(result, ensure_ascii=False, indent=2))
