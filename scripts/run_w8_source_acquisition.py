#!/usr/bin/env python3
"""Acquire exact review text for a frozen W8 source-equivalence pool."""

import argparse
import concurrent.futures
import hashlib
import json
import os
import time
import urllib.error
import urllib.request
from datetime import UTC, datetime
from pathlib import Path


def stamp():
    return datetime.now(UTC).isoformat(timespec='microseconds').replace('+00:00', 'Z')


def key(url):
    return hashlib.sha256(url.encode()).hexdigest()


def atomic(path, value):
    tmp = path.with_suffix(path.suffix + '.tmp')
    tmp.write_text(json.dumps(value, ensure_ascii=False, sort_keys=True) + '\n')
    os.replace(tmp, path)


def acquire(url):
    attempts = []
    for number in range(1, 3):
        started = time.perf_counter()
        began_at = stamp()
        request = urllib.request.Request(
            ENDPOINT,
            data=json.dumps({'url': url}).encode(),
            headers={'Content-Type': 'application/json'},
        )
        try:
            with urllib.request.urlopen(request, timeout=120) as response:
                payload_bytes = response.read()
                payload = json.loads(payload_bytes)
                data = payload.get('data') or {}
                markdown = data.get('markdown')
                attempts.append({
                    'attempt': number,
                    'began_at': began_at,
                    'checked_at': stamp(),
                    'elapsed_ms': round((time.perf_counter() - started) * 1000, 3),
                    'transport_status': response.status,
                    'success': payload.get('success') is True,
                    'error': payload.get('error'),
                    'warning': payload.get('warning'),
                })
                if payload.get('success') is True and isinstance(markdown, str) and markdown:
                    return {
                        'schema_version': 'w8-source-acquisition/1',
                        'url': url,
                        'status': 'acquired',
                        'attempts': attempts,
                        'reviewed_media_type': 'text/markdown',
                        'reviewed_bytes_sha256': hashlib.sha256(markdown.encode()).hexdigest(),
                        'reviewed_text': markdown,
                        'returned_url': data.get('url'),
                        'source': data.get('source'),
                        'metadata': data.get('metadata'),
                        'quality': data.get('quality'),
                    }
                if number == 2:
                    return {
                        'schema_version': 'w8-source-acquisition/1',
                        'url': url,
                        'status': 'unavailable',
                        'attempts': attempts,
                        'reviewed_media_type': None,
                        'reviewed_bytes_sha256': None,
                        'reviewed_text': None,
                        'returned_url': data.get('url'),
                        'source': data.get('source'),
                        'metadata': data.get('metadata'),
                        'quality': data.get('quality'),
                    }
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
            attempts.append({
                'attempt': number,
                'began_at': began_at,
                'checked_at': stamp(),
                'elapsed_ms': round((time.perf_counter() - started) * 1000, 3),
                'transport_status': getattr(exc, 'code', None),
                'success': False,
                'error': f'{type(exc).__name__}: {exc}',
                'warning': None,
            })
    return {
        'schema_version': 'w8-source-acquisition/1',
        'url': url,
        'status': 'unavailable',
        'attempts': attempts,
        'reviewed_media_type': None,
        'reviewed_bytes_sha256': None,
        'reviewed_text': None,
        'returned_url': None,
        'source': None,
        'metadata': None,
        'quality': None,
    }


def run(pool_path, out, endpoint, workers):
    global ENDPOINT
    ENDPOINT = endpoint
    pool = json.loads(pool_path.read_text())
    urls = sorted({item['candidate_url'] for item in pool['candidates']})
    out.mkdir(mode=0o700, parents=True, exist_ok=True)
    records = out / 'records'
    records.mkdir(mode=0o700, exist_ok=True)
    pending = [url for url in urls if not (records / f'{key(url)}.json').exists()]
    completed = len(urls) - len(pending)
    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as executor:
        futures = {executor.submit(acquire, url): url for url in pending}
        for future in concurrent.futures.as_completed(futures):
            url = futures[future]
            try:
                result = future.result()
            except Exception as exc:
                result = {
                    'schema_version': 'w8-source-acquisition/1', 'url': url,
                    'status': 'runner_error', 'attempts': [],
                    'reviewed_media_type': None, 'reviewed_bytes_sha256': None,
                    'reviewed_text': None, 'returned_url': None, 'source': None,
                    'metadata': None, 'quality': None,
                    'runner_error': f'{type(exc).__name__}: {exc}',
                }
            atomic(records / f'{key(url)}.json', result)
            completed += 1
            statuses = {'acquired': 0, 'unavailable': 0, 'runner_error': 0}
            for path in records.glob('*.json'):
                statuses[json.loads(path.read_text())['status']] += 1
            atomic(out / 'progress.json', {
                'schema_version': 'w8-source-acquisition-progress/1',
                'pool_candidate_count': pool['candidate_count'],
                'unique_url_count': len(urls), 'completed': completed,
                'remaining': len(urls) - completed, 'statuses': statuses,
                'updated_at': stamp(),
            })
    print((out / 'progress.json').read_text(), end='')


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--pool', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--endpoint', required=True)
    parser.add_argument('--workers', type=int, default=4, choices=range(1, 9))
    args = parser.parse_args()
    run(args.pool, args.output, args.endpoint, args.workers)


if __name__ == '__main__':
    main()
