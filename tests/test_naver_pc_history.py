"""Naver PC HTML fallback contracts, offline only; no fabricated live-data claims."""
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
import naver_investor as n

NOW = datetime(2026,11,12,17,0,tzinfo=ZoneInfo('Asia/Seoul'))

class Response:
    def __init__(self, payload=None, *, status=200, html=None):
        self.status_code=status
        self._payload=payload
        if html is not None:
            self.content=html.encode('utf8')
            self.headers={'Content-Type':'text/html; charset=utf-8'}
        else:
            self.content=b'{}'
            self.headers={'Content-Type':'application/json'}
    def json(self):return self._payload

class Session:
    def __init__(self,*responses):self.responses=list(responses);self.urls=[]
    def get(self,url,**kw):
        assert kw['allow_redirects'] is False
        assert kw['timeout']==(3,8)
        self.urls.append(url)
        return self.responses.pop(0)

def row(day,foreign=100, institution=-30, personal=-70):
    return {'bizdate':day,'foreignerPureBuyQuant':str(foreign),
            'organPureBuyQuant':str(institution),'individualPureBuyQuant':str(personal)}

def dates(n=23):
    return [(NOW-timedelta(days=i)).strftime('%Y%m%d') for i in range(1,n+1)]

def pc_html(days, *, conflict=False):
    content=''.join(
        f'<tr><td>{d[:4]}.{d[4:6]}.{d[6:]}</td><td>10,000</td><td>0</td><td>0%</td><td>1234</td><td>-30</td><td>{9999 if conflict and i==0 else "+100"}</td><td>1234</td><td>5.1%</td></tr>'
        for i,d in enumerate(days))
    return ('<html><table class="type2"><tr><th>날짜</th><th>종가</th><th>전일비</th><th>등락률</th><th>거래량</th>'
            '<th>기관 순매매량</th><th>외국인 순매매량</th><th>외국인 보유주수</th><th>외국인 보유율</th>'
            '</tr>'+content+'</table></html>')

def test_html_table_parser_uses_only_institution_foreign_shares():
    rec=n._parse_pc_investor_table(pc_html(dates()[:10]).encode())
    assert len(rec)==10
    assert rec[0]=={'bizdate':'20261111','foreignerPureBuyQuant':100,'organPureBuyQuant':-30}
    assert 'individualPureBuyQuant' not in rec[0]


def test_mobile_five_modern_404_front_bad_html_23_returns_20_shares():
    day=dates()
    mobile=[row(d) for d in day[:5]]
    ses=Session(Response({'dealTrendInfos':mobile}),Response(status=404),
                Response({'unexpected':'shape'}),
                Response(html=pc_html(day[:10])),Response(html=pc_html(day[10:20])),
                Response(html=pc_html(day[20:23])))
    x=n.get_naver_flows('005930',NOW,ses)
    assert len(ses.urls)==6
    assert '/item/frgn.naver' in ses.urls[3] and 'page=3' in ses.urls[5]
    assert len(x['daily'])==20
    assert x['aggregate'][2]['foreign']==2000
    assert x['aggregate'][2]['institution']==-600
    assert x['aggregate'][2]['individual'] is None
    assert x['aggregate'][0]['individual']==-350
    assert x['aggregate'][1]['individual'] is None
    assert '외국인 20/20' in x['history_coverage_note']
    assert '개인 5/20' in x['history_coverage_note']
    assert x['independent_source_verified'] is False


def test_pc_html_conflict_prevents_merge_and_preserves_5_day():
    day=dates()
    mobile=[row(d) for d in day[:5]]
    ses=Session(Response({'dealTrendInfos':mobile}),Response(status=404),
                Response({'unexpected':'shape'}),Response(html=pc_html(day[:10],conflict=True)),
                Response(html=pc_html(day[10:20])),Response(html=pc_html(day[20:23])))
    x=n.get_naver_flows('005930',NOW,ses)
    assert len(x['daily'])==5
    assert x['aggregate'][2]['foreign'] is None
    assert '불일치' in x['history_expansion_status']


def test_missing_html_column_cannot_create_fake_personal():
    html='<table><tr><th>날짜</th><th>기관 순매매량</th><th>외국인 순매매량</th></tr><tr><td>2026.11.11</td><td>100</td><td>200</td></tr></table>'
    assert n._parse_pc_investor_table(html.encode())==[]


def test_pc_html_429_or_redirect_never_retried():
    from pytest import raises
    s=Session(Response(status=429,html='blocked'))
    with raises(n.NaverFlowError,match='429'):
        n._fetch_pc_page(s,'005930',1)
    assert len(s.urls)==1
    s2=Session(Response(status=302,html='redirect'))
    with raises(n.NaverFlowError,match='302'):
        n._fetch_pc_page(s2,'005930',1)
    assert len(s2.urls)==1
