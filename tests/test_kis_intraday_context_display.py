"""Display-only KIS current price vs completed-bar decision; never modifies trading engine."""
from pathlib import Path
import subprocess, shutil, json
import pytest

ROOT=Path(__file__).resolve().parents[1]
HTML=(ROOT/'static/index.html').read_text(encoding='utf-8')
APP=(ROOT/'static/app.js').read_text(encoding='utf-8')
FLOW=(ROOT/'static/investor_flow.js').read_text(encoding='utf-8')
SW=(ROOT/'static/sw.js').read_text(encoding='utf-8')


def test_read_only_price_context_is_wired_into_existing_quote_pipeline():
    for identifier in ['intradayContext','intradayQuotePrice','intradayQuoteDiff','intradayRiskNote','analysisPriceOrigin','intradayQuoteMeta']:
        assert f'id="{identifier}"' in HTML
    assert '/static/intraday_context.js' in HTML and '/static/intraday_context.js' in SW
    assert 'window.NOVAIntradayContext?.setAnalysis(d)' in APP
    assert 'window.NOVAFlow?.onAnalysis?.(d.symbol)' in APP
    assert 'window.NOVAIntradayContext?.setQuote(res)' in FLOW
    assert "setQuote(res)" in FLOW
    # Legacy source and trading signal computation must remain independent of the read-only overlay.
    assert 'source:config.source' in APP
    assert 'window.NOVAJournal?.record(d)' in APP


@pytest.mark.skipif(not shutil.which('node'),reason='Node unavailable')
def test_intraday_context_guardrails_and_live_change():
    src=str(ROOT/'static/intraday_context.js')
    node=r'''
      const fs=require('fs'),vm=require('vm');
      const obj={};
      const document={getElementById(id){return obj[id]||(obj[id]={textContent:'',className:''});}};
      const window={};
      const clock=Date.parse('2026-11-23T10:15:00+09:00');
      class TestDate extends Date {static now(){return clock;}}
      const Context={document,window,Date:TestDate,Intl,Number,String,Math};
      vm.runInNewContext(fs.readFileSync(process.argv[1],'utf8'),Context);
      const t=window.NOVAIntradayContext;
      const analysis={symbol:'000660.KS',provider:'Yahoo Finance',reference:1113000,quote:1113000,
        last_completed_bar:'2026-11-20T00:00:00+09:00',long_stop_reference:906143,is_demo:false};
      const snap={symbol:'000660',price:1065000,retrieved_at:'2026-11-23T10:14:50+09:00',trade_time_kst:'10:14:45',realtime_stream:false};
      let r=t.assess(analysis,snap,clock);
      if(r.label!=='하방 위험 관찰'||Math.abs(r.percent+4.3126684636)>1e-6||r.price!==1065000)throw Error('decline classification '+JSON.stringify(r));
      if(t.assess(analysis,{...snap,symbol:'005930'},clock).kind!=='unavailable')throw Error('stock mismatch');
      if(t.assess(analysis,{...snap,retrieved_at:'2026-11-23T10:00:00+09:00'},clock).kind!=='unavailable')throw Error('stale');
      if(t.assess(analysis,{...snap,retrieved_at:'2026-11-19T10:00:00+09:00'},clock).kind!=='unavailable')throw Error('time reversed');
      t.setQuote(snap);t.setAnalysis(analysis);  // response order: quote first, analysis second
      if(!obj.intradayQuoteDiff.textContent.includes('-4.31%'))throw Error('missing percent');
      if(!obj.analysisPriceOrigin.textContent.includes('Yahoo Finance'))throw Error('missing source');
      if(!obj.intradayQuoteMeta.textContent.includes('KIS REST'))throw Error('missing provenance');
      t.setAnalysis({...analysis,symbol:'005930.KS'}); // old quote must not leak to a different ticker
      if(obj.intradayQuotePrice.textContent!=='미확인')throw Error('stale ticker leak');
      t.setQuote({...snap,symbol:'005930',price:1113000});
      if(!obj.intradayQuoteDiff.textContent.includes('0.00%'))throw Error('zero difference');
      t.setUnavailable('KIS 연결 실패');
      if(!obj.intradayRiskNote.textContent.includes('KIS 연결 실패'))throw Error('outage not visible');
      const rise=t.assess(analysis,{...snap,price:1175000},clock);
      if(rise.kind!=='up')throw Error('upward change');
      const cross=t.assess(analysis,{...snap,price:900000},clock);
      if(!cross.stopCrossed||cross.kind!=='down')throw Error('reference stop');
      process.stdout.write('ok');
    '''
    proc=subprocess.run(['node','-e',node,src],text=True,capture_output=True)
    assert proc.returncode==0,proc.stderr
    assert proc.stdout=='ok'


def test_no_automatic_trade_or_server_change_from_display_only_update():
    assert 'POST /api/orders' not in (ROOT/'static/intraday_context.js').read_text(encoding='utf-8')
    assert 'source=kis' not in APP
