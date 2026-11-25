"""NOVA v3.9 read-only stock headline lookup (not a sentiment trading signal).

Preferred: official NAVER API HUB using server-side credentials.
Fallback: public Google News RSS search (best effort; can fail or change).
Never publish article bodies, cache tokens, or send API keys to the browser.
"""
from __future__ import annotations

from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from html import unescape
import os
import re
from urllib.parse import urlsplit, quote
from xml.etree import ElementTree as ET
import requests
from stock_lookup import FALLBACK

HUB_URL = 'https://naverapihub.apigw.ntruss.com/search/v1/news'
GOOGLE_RSS = 'https://news.google.com/rss/search'
NEWS_QUERY = re.compile(r'^[\w\s.&()+\-가-힣]{1,48}$', re.UNICODE)
KNOWN_US = {'AAPL':'애플 Apple', 'TSLA':'테슬라 Tesla', 'NVDA':'엔비디아 NVIDIA', 'MSFT':'마이크로소프트 Microsoft', 'GOOGL':'알파벳 Google', 'AMZN':'아마존 Amazon', 'META':'메타 Meta'}

class NewsUnavailable(Exception):
    pass


def search_terms(symbol: str, name: str = '') -> str:
    """Never misrepresent a ticker-only search as company-matched news."""
    if not isinstance(symbol, str) or not isinstance(name, str):
        raise ValueError('검색어 형식 오류')
    symbol = symbol.strip().upper()
    name = name.strip()
    if len(symbol) > 24 or len(name) > 48:
        raise ValueError('검색어가 너무 깁니다')
    if name:
        if not NEWS_QUERY.fullmatch(name):
            raise ValueError('종목명은 한글, 영문, 숫자 등 48자 이내로 입력하세요')
        return name
    for stock_name, code, _ in FALLBACK:
        if code == symbol:
            return stock_name
    if symbol in KNOWN_US:
        return KNOWN_US[symbol]
    if re.fullmatch(r'[A-Z]{1,8}(?:[.=-][A-Z]{1,5})?', symbol):
        return symbol
    # For an unknown Korean code there is no verified company name; let user supply one.
    raise ValueError('해당 종목의 이름을 검색 결과에서 선택한 뒤 종목뉴스를 조회하세요.')


def _clean_text(raw) -> str:
    return re.sub(r'\s+', ' ', unescape(re.sub(r'<[^>]*>', ' ', str(raw or '')))).strip()[:240]


def _link(raw: str) -> str:
    if not isinstance(raw, str) or len(raw) > 1500:
        return ''
    try:
        u = urlsplit(raw.strip())
        return raw.strip() if u.scheme == 'https' and u.netloc and not (u.username or u.password) else ''
    except ValueError:
        return ''


def _iso_date(value: str) -> str:
    try:
        dt = parsedate_to_datetime(value)
        return (dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt).astimezone(timezone.utc).isoformat(timespec='seconds')
    except (ValueError, TypeError, IndexError):
        return ''


def normalize_naver(payload: dict) -> list[dict]:
    if not isinstance(payload, dict):
        raise NewsUnavailable('뉴스 응답 형식이 올바르지 않습니다.')
    out=[]; seen=set()
    for item in payload.get('items', [])[:30]:
        if not isinstance(item,dict):continue
        title=_clean_text(item.get('title'))
        link=_link(item.get('originallink')) or _link(item.get('link'))
        if not title or not link:continue
        sig=re.sub(r'\W+', '', title).casefold()
        if sig in seen:continue
        seen.add(sig)
        out.append({'title':title,'link':link,'publisher':'언론사 원문','published_at':_iso_date(item.get('pubDate',''))})
    return out[:20]


def normalize_rss(xml: bytes) -> list[dict]:
    if len(xml) > 450_000:
        raise NewsUnavailable('RSS 응답 크기 제한을 초과했습니다.')
    try:
        root=ET.fromstring(xml)
    except ET.ParseError as exc:
        raise NewsUnavailable('RSS 데이터 형식 오류') from exc
    seen=set();items=[]
    for item in root.findall('./channel/item')[:30]:
        title=_clean_text(item.findtext('title'))
        link=_link(item.findtext('link') or '')
        source=item.find('source')
        publisher=_clean_text(source.text if source is not None else '') or 'Google 뉴스'
        if not title or not link or urlsplit(link).hostname not in {'news.google.com','www.google.com'}:continue
        sig=re.sub(r'\W+', '', title).casefold()
        if sig in seen:continue
        seen.add(sig)
        items.append({'title':title,'link':link,'publisher':publisher,'published_at':_iso_date(item.findtext('pubDate') or '')})
    return items[:20]


def fetch_news(symbol: str, name: str = '') -> dict:
    term=search_terms(symbol,name)
    now=datetime.now(timezone.utc).isoformat(timespec='seconds')
    cid=os.environ.get('NAVER_NEWS_CLIENT_ID','').strip()
    secret=os.environ.get('NAVER_NEWS_CLIENT_SECRET','').strip()
    if cid and secret:
        headers={'X-NCP-APIGW-API-KEY-ID':cid,'X-NCP-APIGW-API-KEY':secret,'Accept':'application/json'}
        try:
            r=requests.get(HUB_URL, params={'query':term,'display':20,'sort':'date','format':'json'},headers=headers,timeout=8,allow_redirects=False)
            if r.status_code != 200:
                raise NewsUnavailable('NAVER API HUB 응답 오류')
            items=normalize_naver(r.json())
        except (requests.RequestException,ValueError,NewsUnavailable) as exc:
            raise NewsUnavailable('NAVER API HUB 뉴스 조회 실패. 인증 정보·이용 한도·네트워크를 확인하세요.') from exc
        return {'query':term,'symbol':symbol,'provider':'NAVER API HUB (공식 검색 API)','provider_type':'naver_api_hub',
                'retrieved_at':now,'items':items,'warning':'기사 발행 시각·정확성은 언론사와 검색 제공원의 정보에 따릅니다. 기사 제목만으로 투자 신호를 생성하지 않습니다.'}
    # Optional public RSS fallback, transparently identified as a non-contractual source.
    try:
        r=requests.get(GOOGLE_RSS,params={'q':f'"{term}" 주식','hl':'ko','gl':'KR','ceid':'KR:ko'},
                       headers={'User-Agent':'Mozilla/5.0 (compatible; NOVA-ReadOnly-News/3.9)'},timeout=8)
        r.raise_for_status()
        items=normalize_rss(r.content)
    except (requests.RequestException, NewsUnavailable) as exc:
        raise NewsUnavailable('공개 뉴스 RSS를 조회하지 못했습니다. 뉴스 원문 검색 링크를 이용하거나 NAVER API HUB 키를 설정해 주세요.') from exc
    return {'query':term,'symbol':symbol,'provider':'Google 뉴스 RSS (공개 피드 · 비공식 연동)','provider_type':'google_rss',
            'retrieved_at':now,'items':items,'warning':'공개 RSS는 뉴스 제공·지속성을 보장하지 않습니다. 제목과 원문 링크만 표시하며 매수·매도 신호에는 반영하지 않습니다.'}
