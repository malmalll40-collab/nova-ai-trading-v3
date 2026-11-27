"""Run the finite owner-authorized import and download its verified private file."""
import argparse
import hashlib
import io
import json
import os
from pathlib import Path
import time
from urllib.parse import urlsplit
import zipfile

import requests


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('url', help='NOVA HTTPS origin')
    parser.add_argument('output', type=Path)
    args = parser.parse_args()
    url = urlsplit(args.url)
    if url.scheme != 'https' or not url.hostname or url.username or url.password or url.path not in ('','/') or url.query or url.fragment:
        parser.error('An HTTPS origin is required')
    token = os.environ['NOVA_KRX_TRANSFER_TOKEN']
    base = args.url.rstrip('/') + '/api/research/krx/bulk'
    with requests.Session() as session:
        session.headers['Authorization'] = 'Bearer ' + token
        while True:
            response = session.post(base, timeout=(12, 240), allow_redirects=False)
            if response.status_code != 200:
                detail = response.json().get('detail') if response.headers.get('content-type','').startswith('application/json') else None
                raise SystemExit(f'KRX operation stopped: HTTP {response.status_code} {detail or ""}')
            info = response.json()
            print(json.dumps(info, ensure_ascii=False), flush=True)
            if info['status'] == 'PUBLISHED':
                break
            if info['status'] == 'BUSY':
                time.sleep(2)
            elif info['status'] != 'STAGING':
                raise SystemExit('Unexpected import state')
        result = session.get(base + '/download', timeout=(12, 180), allow_redirects=False)
        if result.status_code != 200:
            raise SystemExit(f'Private export unavailable: HTTP {result.status_code}')
    with zipfile.ZipFile(io.BytesIO(result.content)) as archive:
        report = json.loads(archive.read('validation.json'))
        if report['database_sha256'] != info['sha256'] or report['rows'] != info['expected_rows']:
            raise ValueError('Release/ZIP mismatch')
        for name, expected in report['files_sha256'].items():
            if hashlib.sha256(archive.read(name)).hexdigest() != expected:
                raise ValueError('File checksum mismatch')
    import tempfile
    with tempfile.NamedTemporaryFile(dir=args.output.parent, delete=False) as tmp:
        tmp.write(result.content)
        tmp_path = tmp.name
    os.replace(tmp_path, args.output)
    print(f'{report["rows"]} rows verified; {args.output}', flush=True)


if __name__ == '__main__':
    main()
