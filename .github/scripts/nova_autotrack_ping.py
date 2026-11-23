"""GitHub-hosted scheduler for NOVA's read-only, auth-gated research job.

No provider credentials or database URLs enter GitHub Actions. All market data
access runs on the user's existing Render backend. GitHub only stores the
independent autotrack trigger token in an encrypted repository Actions secret.
"""
from __future__ import annotations
import json
import os
import sys
import time
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

BASE='https://nova-ai-trading-v3-mobile.onrender.com'
PATH='/api/autotrack/'
TOKEN=os.environ.get('NOVA_AUTOTRACK_TOKEN','')


def request(path,method='GET'):
    url=BASE+PATH+path
    req=Request(url,method=method,headers={'X-Nova-Autotrack-Token':TOKEN,
                 'Accept':'application/json','User-Agent':'NOVA-GitHub-Cron/1.0'})
    with urlopen(req,timeout=100) as response:
        return json.loads(response.read(4096).decode('utf-8'))


def main():
    if len(TOKEN)<24:
        print('ERROR: Add NOVA_AUTOTRACK_TOKEN to GitHub Actions secrets (same value as Render).')
        return 2
    # Render Free can be asleep, so warm via the actual authenticated run endpoint.
    for attempt in range(3):
        try:
            result=request('run','POST')
            break
        except (HTTPError,URLError,TimeoutError) as err:
            # Do not print URLs, request headers, credentials, or upstream details.
            print(f'Start request failed: {type(err).__name__}, attempt {attempt+1}/3')
            if attempt==2:return 3
            time.sleep(20)
    print('Scheduled run accepted:',result.get('status'))
    if result.get('status') not in ('queued','already_running'):
        return 4
    # GitHub 1 shared request every 25 s keeps the service active while it works.
    # Bounded to 25 min; a restart, timeout or missing publication shows as failure.
    for count in range(60):
        time.sleep(25)
        try:
            job=request('job')
        except (HTTPError,URLError,TimeoutError):
            print('Polling interrupted, retrying')
            continue
        state=job.get('status')
        print('Job status:',state,'poll',count+1)
        if state in ('scanning','evaluating','queued'):
            continue
        if state in ('complete','partial'):
            data=job.get('result') or {}
            print('Scan:',(data.get('scan') or {}).get('status'),
                  'New outcomes:',(data.get('evaluation') or {}).get('outcomes_new'))
            # Never count skipped/unavailable market discovery as a successful daily scan.
            if state!='complete' or (data.get('scan') or {}).get('status') in ('failed','skipped'):
                print('FAILED OR PARTIAL: inspect NOVA auto-evidence status; no invented observations')
                return 5
            return 0
        if state in ('idle',None):
            print('Worker lost/restarted before the run completed')
            return 6
        print('Unexpected scheduler state:',state)
        return 7
    print('Timed out; the job may have stopped or become too slow')
    return 8


if __name__=='__main__':sys.exit(main())
