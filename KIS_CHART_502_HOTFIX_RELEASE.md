# NOVA v4.5 KIS 차트 502 오류 수정·검증 보고서

작성일: 2026-11-21 (KST)

## 원인 확인

2026-11-21 운영 로그에서 KIS `/api/market/quote?symbol=005930`는 20초 간격으로 200 OK를 반환했으나, `/api/chart/history?symbol=005930.KS&source=kis&timeframe=1d` 및 `1w`는 502 응답했습니다.

원인은 현재 배포 ZIP의 `server.py`에서 확인한 **데이터 타입 불일치**입니다.

- 원래 코드: `_cached(key, lambda: fetch_kis_native_period(...), ttl=900)` → `chart_data(bars, ...)`.
- `_cached()`는 **사전(dict) 전용**으로 구현되어 `dict(result, cached=..., cache_age_seconds=...)`를 반환합니다.
- 따라서 `fetch_kis_native_period()`가 정상적으로 `pandas.DataFrame`을 반환해도 사전으로 바뀌고, `chart_data()` 내부 `grouped_ohlcv()`가 `.empty` 등 DataFrame 연산을 할 때 예외가 발생합니다.
- 예외는 공통 502로 변환되어 KIS 인증 오류처럼 보였습니다.

이 데이터 타입 오류는 KIS가 정상 시세를 반환해도 차트가 실패하게 하는 **확정된 코드 결함**입니다. 단, 다른 KIS 제공원 오류가 추가로 존재하는지는 배포 후 검증해야 합니다.

## 수정 사항

1. `_cached`에 직접 DataFrame을 전달하지 않고 `{'bars': DataFrame}` 형태로 전달하여 원형을 보존합니다.
2. 신규 캐시 키 `v45-kis-native-period-df-v2`를 사용해 이전 형식과 충돌하지 않도록 합니다.
3. 추후 제공원 오류가 발생하면 인증키·원본 메시지·헤더를 노출하지 않고, 허용된 KIS 오류 코드 또는 예외 타입만 서버 로그와 인증된 차트 응답에 표시합니다.
4. 기존 KIS 기간별 조회 API, 시세 인증, 20초 진행 중 봉 갱신 로직, 기존 BUY/SELL, 종목추천 등은 변경하지 않습니다.

## 검증

- 기존 운영 ZIP에서 새 회귀 테스트를 실행하면 **1d 조회 시 HTTP 502를 재현**합니다 (동일한 가상 정상 DataFrame 공급원). 수정판에서 같은 테스트는 200을 반환합니다.
- 전체 pytest: **337 passed**.
- 회귀 테스트: `tests/test_kis_chart_502_regression.py` 신규 4개.
- mock DataFrame을 사용해 KIS 1d/1w/1mo/1y에서 첫 요청과 캐시 재요청 모두 200, 정상 봉 배열 수신 확인.
- 시세 공급원 실패 시 허위 OHLCV 반환 없이 502 유지, 환경변수 누락 시 503 유지.
- 제공원 오류 코드 `EGW00201`만 안전하게 안내하고 원문 비밀 문자열은 숨기도록 확인.
- 기존 국내주식 분석·추천·차트 코드 전체 회귀 테스트 통과.

## 운영 검증 필요

- 실제 KIS APP Key/Secret은 이 패키지에 포함되지 않습니다.
- 2026-11-21은 토요일이므로 장중 실제 현재 봉 갱신까지 검증할 수 없습니다.
- 운영 GitHub ZIP 파일을 **한 개만 남기도록** 교체하고 Render가 `Live`가 되면 아래 API를 NOVA 로그인 상태에서 확인하세요:
  - `/api/chart/history?symbol=005930.KS&source=kis&timeframe=1d`
  - `/api/chart/history?symbol=005930.KS&source=kis&timeframe=1w`
  - `/api/chart/history?symbol=005930.KS&source=kis&timeframe=1mo`
  - `/api/chart/history?symbol=005930.KS&source=kis&timeframe=1y`
  - 인증이 필요한 API이므로 미로그인 401은 별도 상태입니다.
- 200 응답이라도 마지막 거래일, OHLCV 유효성, 실제 데이터 제공원, 캐시 시간을 확인해야 합니다. 새 버전 배포 전 KIS 실데이터 성공 판정은 불가합니다.

## 사용한 기준

- 한국투자증권 공식 `inquire-daily-itemchartprice` TR_ID FHKST03010100 및 D/W/M/Y 조회 방식
  https://github.com/koreainvestment/open-trading-api/blob/main/examples_llm/domestic_stock/inquire_daily_itemchartprice/inquire_daily_itemchartprice.py
- 데이터 변경: `server.py`만 수정. `tests/test_kis_chart_502_regression.py`, `KIS_CHART_502_HOTFIX_RELEASE.md`, `RELEASE_SHA256.json` 추가·갱신.
