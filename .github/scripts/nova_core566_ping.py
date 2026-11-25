"""Trigger prospective collector and report completion; never print credentials."""
import os
import sys
import time
import json
from datetime import datetime
from zoneinfo import ZoneInfo
from urllib.request import Request, urlopen
from urllib.error import HTTPError

BASE = 'https://nova-ai-trading-v3-mobile.onrender.com/api/research/core566/'
KST = ZoneInfo('Asia/Seoul')


def call(path, token, method='GET'):
    with urlopen(Request(BASE+path, method=method, headers={'X-Nova-Autotrack-Token': token}), timeout=85) as r:
        return json.load(r)


def main():
    token = os.getenv('NOVA_AUTOTRACK_TOKEN', '')
    if len(token) < 24:
        print('Scheduler secret missing')
        return 2
    now = datetime.now(KST)
    day = now.date().isoformat()
    if now.weekday() >= 5 or now.hour < 18:
        print('Outside same-day collection window')
        return 0
    deadline = time.monotonic()+1200
    for attempt in range(3):
        try:
            start = call('collect', token, 'POST')
            if start.get('day') != day:
                print('Collection date mismatch')
                return 3
            if start.get('status') == 'skipped' and start.get('reason') == 'HOLIDAY':
                print('Verified exchange holiday; no collection')
                return 0
            if start.get('status') in ('blocked', 'skipped'):
                reason = start.get('reason')
                allowed = ('DATE_MISMATCH', 'BEFORE_WINDOW', 'CALENDAR_UNVERIFIED',
                           'CALENDAR_DATE_MISMATCH', 'MARKET_NOT_CLOSED', 'SESSION_UNVERIFIED')
                print('Collection blocked:', reason if reason in allowed else 'UNSPECIFIED')
                if attempt == 2:
                    return 4
                time.sleep(20)
                continue
            if start.get('status') != 'running':
                print('Unexpected start status')
                return 3
            break
        except HTTPError as exc:
            if exc.code in (401, 403, 503):
                print('Authorization or collector setup unavailable:', exc.code)
                return 2
        except Exception:
            pass
        if attempt == 2:
            print('Start failed')
            return 3
        time.sleep(20)
    while time.monotonic() < deadline and datetime.now(KST).date().isoformat() == day:
        time.sleep(25)
        try:
            r = call('job', token)
        except Exception:
            continue
        if r.get('day') != day:
            print('Job date mismatch')
            return 4
        status = r.get('status')
        print('status', status, 'processed', len(r.get('results') or {}))
        if status == 'complete':
            return 0
        if status != 'running':
            return 4
    print('Collection incomplete')
    return 5


if __name__ == '__main__':
    sys.exit(main())
