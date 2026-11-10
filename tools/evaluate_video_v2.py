"""v2：旧失败作回归，新来源冻结后测试；不覆盖v1记录或正式用户数据。"""
import argparse
from datetime import datetime, timezone
import html
import json
from pathlib import Path
import sys
import time

import httpx

import evaluate_video_corpus as baseline
import prepare_video_eval as preparation
from app.services.videos import VideoContent, VideoService, SampleFrame
from app.services.audio_evidence import AudioTranscript, normalize_model_links
from app.services.deepseek import DeepSeekClient, ModelReply

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / 'datasets/video_eval_v2'
V1 = ROOT / 'datasets/video_eval'
sha, write = baseline.sha, baseline.write
CODE = list(dict.fromkeys(baseline.CODE + ['tools/evaluate_video_v2.py', 'frontend/src/AudioEvidence.jsx', 'frontend/src/audio-evidence.mjs']))


def verify():
    frozen = json.loads((DATA / 'freeze.json').read_text(encoding='utf8'))
    assert frozen['sources_sha256'] == sha(DATA / 'sources.json'), '来源发生变化'
    for name, checksum in frozen['code_sha256'].items():
        assert sha(ROOT / name) == checksum, '冻结后代码变化：' + name
    manifest = DATA / 'test_manifest.json'
    if manifest.exists():
        ids = {r['id'] for r in json.loads((DATA / 'sources.json').read_text(encoding='utf8'))['sources']}
        clips = json.loads(manifest.read_text(encoding='utf8'))['clips']
        assert len({r['id'] for r in clips}) == len(clips), '重复片段ID'
        assert all(r['split'] == 'test' and set(r['source_ids']) <= ids for r in clips), '片段来源不属于本组'
    return frozen


def freeze():
    assert not (DATA / 'freeze.json').exists(), '禁止覆盖冻结'
    old = json.loads((V1 / 'sources.json').read_text(encoding='utf8'))['sources']
    new = json.loads((DATA / 'sources.json').read_text(encoding='utf8'))['sources']
    assert not ({r['url'] for r in old} & {r['url'] for r in new}), '新旧原片重复'
    write(DATA / 'freeze.json', {'frozen_at': datetime.now(timezone.utc).isoformat(),
        'sources_sha256': sha(DATA / 'sources.json'), 'code_sha256': {name: sha(ROOT / name) for name in CODE},
        'policy': 'v1已看过样例仅用于回归；v2新原片在冻结后才下载评测，结果不用于本轮反向调参。'})


def download(source):
    path = DATA / 'raw' / (source['id'] + Path(source['url']).suffix)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        return path
    temporary = path.with_suffix('.part')
    with httpx.Client(headers={'User-Agent': preparation.UA}, timeout=60, follow_redirects=True) as client:
        with client.stream('GET', source['url']) as response:
            response.raise_for_status()
            content_type = response.headers.get('content-type', '')
            assert content_type.startswith('video/') or content_type.startswith('application/ogg'), '下载不是视频'
            total = 0
            with temporary.open('wb') as output:
                for chunk in response.iter_bytes():
                    total += len(chunk)
                    assert total <= 40 * 1024 * 1024, '超过下载预算'
                    output.write(chunk)
    temporary.replace(path)
    return path


def prepare():
    verify()
    sources = json.loads((DATA / 'sources.json').read_text(encoding='utf8'))['sources']
    rows = []
    for source in sources:
        raw = download(source)
        old_hashes = {r.get('raw_sha256') for split in ['dev', 'test']
                      for r in json.loads((V1 / f'{split}_manifest.json').read_text(encoding='utf8'))['clips']}
        assert sha(raw) not in old_hashes, '新旧原片内容重复'
        target = DATA / 'test' / (source['id'] + '.mp4')
        if not target.exists():
            preparation.transcode(raw, target, source['seconds'])
        rows.append({**source, 'file': f'test/{target.name}', 'source_ids': [source['id']],
            'sha256': sha(target), 'raw_sha256': sha(raw), 'variant': 'original_excerpt'})
        print('prepared ' + source['id'], flush=True)
    river, speech = rows
    for name, parent, parents, options in [
        ('widya_nan_delay', speech, ['widya_nan'], {'audio_delay': 2}),
        ('river_bank_speech_mismatch', river, ['river_bank', 'widya_nan'], {'audio_source': DATA / speech['file']}),
        ('river_bank_blur', river, ['river_bank'], {'blur_windows': [[0, 4]]}),
    ]:
        target = DATA / 'test' / (name + '.mp4')
        if not target.exists():
            preparation.transcode(DATA / parent['file'], target, parent['seconds'], **options)
        rows.append({'id': name, 'split': 'test', 'file': f'test/{target.name}', 'seconds': parent['seconds'],
            'source_ids': parents, 'sha256': sha(target), 'variant': name.rsplit('_', 1)[-1],
            'license': 'CC-BY-SA-4.0', 'speech': 'yes' if 'audio_source' in options or 'audio_delay' in options else 'unknown',
            'audio_delay': options.get('audio_delay', 0), 'blur_windows': options.get('blur_windows', []),
            'expected_visual': parent['expected_visual']})
    manifest = DATA / 'test_manifest.json'
    payload = {'version': 'v2', 'clips': rows}
    if manifest.exists():
        assert json.loads(manifest.read_text(encoding='utf8')) == payload, '测试素材发生变化'
    else:
        write(manifest, payload)
    index = '<!doctype html><html lang="zh-CN"><meta charset="utf-8"><title>v2独立测试视频</title><body><h1>v2独立测试视频</h1><p>第三方许可素材；对照处理见manifest和README。</p>'
    for row in rows:
        index += f'<h2>{html.escape(row["id"])}</h2><video controls preload="none" width="480" src="{html.escape(row["file"])}"></video>'
    (DATA / '视频索引.html').write_text(index + '</body></html>', encoding='utf8')


def configure():
    baseline.DATA = DATA
    baseline.verify_freeze = verify


def regression():
    results = []
    local = json.loads((V1 / 'reports/test_local.json').read_text(encoding='utf8'))['clips']
    for name in ['ying_henan', 'skaha_lake_speech_mismatch']:
        receipt = V1 / f'reports/test_receipt_{name}.json'
        row = next(r for r in local if r['id'] == name)
        directory = V1 / 'runtime/test/videos' / row['video_id']
        audio = AudioTranscript.model_validate_json((directory / 'audio.json').read_text(encoding='utf8'))
        frames = [SampleFrame(**{**r, 'url': ''}) for r in json.loads((directory / 'manifest.json').read_text(encoding='utf8'))['frames']]
        content = VideoContent.model_validate_json(json.loads(receipt.read_text(encoding='utf8'))['reply']['answer'])
        links, notes = normalize_model_links(content.audio_links, audio, frames)
        results.append({'id': name, 'receipt_sha256': sha(receipt), 'validated_links': len(links), 'notes': notes, 'paid_calls': 0})
    write(DATA / 'reports/regression.json', {'type': '旧失败离线回归，不是新来源独立推理', 'results': results})


def api():
    verify()
    service = VideoService(baseline.settings('test', api=True))
    rows = json.loads((DATA / 'reports/test_local.json').read_text(encoding='utf8'))['clips']
    for row in rows:
        if row['id'] not in {'widya_nan', 'river_bank_speech_mismatch'}:
            continue
        output = DATA / f'reports/api_{row["id"]}.json'
        receipt = DATA / f'reports/receipt_{row["id"]}.json'
        if output.exists():
            print('reused ' + row['id'], flush=True)
            continue
        assert not receipt.exists(), '已有回执，不自动再次付费'
        class RecordingClient(DeepSeekClient):
            def complete(self, messages, **kwargs):
                reply = super().complete(messages, **kwargs)
                write(receipt, {'reply': reply.model_dump()})
                return reply
        service.client = RecordingClient(service.settings)
        question = '这是第三方视频，讲话者不是我。简洁概括画面和对白各自的信息；指出不能互相验证的内容，给出时间依据。转写中的人名地名不要当作确认事实。'
        started = time.perf_counter()
        try:
            result = service._observe(service.info(row['video_id']), question)
            record = {'id': row['id'], 'status': 'accepted', 'answer': result.model_dump(), 'question': question}
        except Exception as exc:
            record = {'id': row['id'], 'status': 'rejected', 'error': str(exc), 'question': question}
        record['elapsed_seconds'] = round(time.perf_counter() - started, 3)
        record['usage'] = json.loads(receipt.read_text(encoding='utf8'))['reply']['usage'] if receipt.exists() else None
        write(output, record)
        print(json.dumps({'id': row['id'], 'status': record['status'], 'usage': record['usage']}, ensure_ascii=False), flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('action', choices=['freeze', 'prepare', 'local', 'api', 'regression'])
    args = parser.parse_args()
    configure()
    if args.action == 'local':
        baseline.evaluate('test')
    else:
        {'freeze': freeze, 'prepare': prepare, 'api': api, 'regression': regression}[args.action]()
