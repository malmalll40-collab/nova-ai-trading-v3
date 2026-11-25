"""Trigger read-only collection and check completion; secrets never logged."""
import os,time,json,sys
from urllib.request import Request,urlopen
BASE='https://nova-ai-trading-v3-mobile.onrender.com/api/research/core561/'
TOKEN=os.environ.get('NOVA_AUTOTRACK_TOKEN','')
def call(path,method='GET'):
    with urlopen(Request(BASE+path,method=method,headers={'X-Nova-Autotrack-Token':TOKEN}),timeout=100) as r:return json.load(r)
def main():
    if len(TOKEN)<24:print('Missing scheduler secret');return 2
    for attempt in range(3):
        try:call('collect','POST');break
        except Exception:
            if attempt==2:print('Start failed');return 3
            time.sleep(20)
    for _ in range(60):
        time.sleep(25)
        try:r=call('job')
        except Exception:continue
        print('status',r.get('status'),'processed',len(r.get('results',{})))
        if r.get('status') in ('complete','skipped'):return 0
        if r.get('status')!='running':return 4
    return 5
if __name__=='__main__':sys.exit(main())
