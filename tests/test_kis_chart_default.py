"""KIS display defaults without changing the independent trade signal provider."""
import json
import shutil
import subprocess
from pathlib import Path

import pytest

ASSETS=Path(__file__).resolve().parents[1]/'static'


@pytest.mark.parametrize('saved,expected_provider,expected_timeframe',[
    ({},'kis','1d'),
    ({'nova4-chart-provider':'analysis','nova4-chart-timeframe':'analysis'},'kis','1d'),
    ({'nova4-chart-provider':'analysis','nova4-chart-timeframe':'1w'},'kis','1w'),
    ({'nova45-kis-chart-default-v1':'1','nova4-chart-provider':'analysis','nova4-chart-timeframe':'analysis'},'analysis','analysis'),
    ({'nova45-kis-chart-default-v1':'1','nova4-chart-provider':'kis','nova4-chart-timeframe':'1y'},'kis','1y'),
])
def test_browser_persistent_settings_migrate_once_and_respect_manual_choice(saved,expected_provider,expected_timeframe):
    if not shutil.which('node'):
        pytest.skip('Node.js not available in test environment')
    script='''
      const vm=require('vm'), fs=require('fs');
      const b=new Map(Object.entries(JSON.parse(process.argv[1])));
      const localStorage={getItem:k=>b.has(k)?b.get(k):null,setItem:(k,v)=>b.set(k,String(v))};
      const window={};
      const filename=process.argv[2];
      let source=fs.readFileSync(filename,'utf8');
      const original='window.NOVACandles={draw,render,valid,selectPeriod};';
      if(!source.includes(original))throw Error('chart state test hook missing');
      source=source.replace(original,'window.chartInit={provider,timeframe};'+original);
      vm.runInNewContext(source,{window,localStorage});
      process.stdout.write(JSON.stringify({initial:window.chartInit,stored:Object.fromEntries(b)}));
    '''
    result=subprocess.run(['node','-e',script,json.dumps(saved),str(ASSETS/'candles.js')],capture_output=True,text=True,check=True)
    data=json.loads(result.stdout)
    assert data['initial']=={'provider':expected_provider,'timeframe':expected_timeframe}
    if not saved.get('nova45-kis-chart-default-v1'):
        assert data['stored']['nova45-kis-chart-default-v1']=='1'
        assert data['stored']['nova4-chart-provider']=='kis'


def test_initial_kis_view_is_decoupled_from_trading_analysis():
    js=(ASSETS/'candles.js').read_text(encoding='utf-8')
    app=(ASSETS/'app.js').read_text(encoding='utf-8')
    html=(ASSETS/'index.html').read_text(encoding='utf-8')
    assert "let provider='kis';" in js
    assert "let timeframe='1d';" in js
    assert "const actualSource=provider==='kis'?'kis':base.source;" in js
    assert 'value="kis" selected' in html
    assert 'source:config.source' in app  # analysis mode retains separate setting
    assert 'onViewChange(view)' in app  # quote refreshed on successful manual login
