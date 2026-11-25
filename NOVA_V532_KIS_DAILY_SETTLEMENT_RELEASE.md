# NOVA v5.3.2 — KIS 최신 완료 일봉 검증·안전 복구

## 배포 범위

- **기반:** v5.3.1 통합 소스 그대로 유지. 새로운 `daily_settlement_guard.py`와 데이터 소스/분석·차트 라우트/진단 화면만 보강.
- **목표:** 2026-11-24 삼성전자 가격 불일치 사례(과거 KIS 차트 종가 184,100원, Yahoo 기본 종가 189,400원)를 자동 감지하여 불확실한 일봉을 매매 신호·일봉 차트 확정봉으로 사용하지 않게 한다.
- **매매 정책:** 기존 13개 역할, 5-Core, AI 연구 및 Neon 기록 구조 변경 없음. 데이터 검증 실패·보류일 때만 **신규 BUY/SELL의 실행 가능 상태를 보류**한다. 자동 주문 기능은 포함하지 않는다.

## 어떻게 검사하나

1. KIS 일봉 차트(`FHKST03010100`)에서 최근 완료 거래일 OHLCV 확인.
2. 동일 날짜 KIS 최근 일별 시세(`FHKST01010400`, 수정주가 플래그 `0`)와 Yahoo **기본/비조정** OHLC를 함께 비교. 시가·고가·저가·종가의 상대 차이 0.5%를 허용한다.
3. KIS와 두 참조원 사이에 충돌이 있으면 KIS 원래 차트 엔드포인트를 **한 번만 재조회**한다. 재조회한 KIS 봉이 두 참조원에 맞을 때에만 그 **동일 KIS 차트 데이터**로 최신 봉을 교체한다.
4. 끝까지 맞지 않거나 자료가 없으면 해당 최신 봉을 **격리**하고 기존 KRX 완료 거래일 규칙과 함께 **신규 신호 보류** 처리한다. Yahoo 봉을 기존 KIS 과거 데이터에 결합하거나 가격 비율로 보정하지 않는다.
5. 오늘 봉은 18:00 KST 이전에는 ‘정산/검증 대기’로 다룬다. **18:00은 보수적 검사 시작 시각이지 거래소의 공인 데이터 확정시간이 아니다.**
6. 오래된 원본 일봉과 휴장일의 거래일 검증은 기존 `completed_swing_bars` 검사에 맡긴다. 2025-10-02 같은 **과거 예외 날짜의 원인은 자동 수정하지 않는다.**

## 상태코드

- `VERIFIED` : KIS 차트·KIS 일별·Yahoo 기본 OHLC가 같은 날짜에 허용 범위 내 일치
- `REQUERY_RECOVERED` : KIS 차트 재조회 결과로 동일 공급원 봉 복구
- `PRICE_CONFLICT` : 가격 불일치가 지속되어 최신 봉 격리
- `UNVERIFIED` : 검증용 자료 부족 또는 공급원 조회 실패로 최신 봉 격리
- `SETTLEMENT_PENDING` : 오늘 봉은 검사 시각 이전이므로 보류
- `OLD_DATA` : 마지막 봉이 10일 이상 오래됨. 기존 KRX 거래일 갱신성 검증에 따름

## 운영에서 확인

1. GitHub 저장소 루트 ZIP이 **정확히 하나**인지 확인 후 새 파일로 교체. `.github` 폴더는 유지한다.
2. Render 배포 완료(`Live`) 및 `/healthz`의 `app_version: 5.3.2` 확인.
3. NOVA → 삼성전자 → 일봉 스윙 분석 → 검증 → **KIS 최신 일봉 재검증 실행**.
4. `VERIFIED`면 최근 완료봉 검증 성공; `REQUERY_RECOVERED`면 KIS 재조회 복구; `PRICE_CONFLICT`/`UNVERIFIED`면 **안전 보류**가 정상.
5. **KIS · Yahoo 가격 비교 실행**도 기존대로 사용 가능. 11월 24일 문제 날짜의 값과 10월 2일 차이를 따로 확인할 수 있다.
6. KIS 일봉 차트 선택 시 동일 검증 상태가 응답에 표시되고 불일치한 최신 확정봉을 빼고 보여준다. 화면에서 KIS 20초 시세 스냅샷은 확정된 종가로 대체되지 않는다.

## 제약과 주의

- 운영용 KIS 키로 실제 두 엔드포인트를 호출한 결과는 ZIP 제작 당시 **확인되지 않았다.** 인증·호출 제한·시간외 시세 처리에 대한 운영 검증은 배포 후 필요하다.
- 두 참조원 사이의 가격 차이가 없다는 것은 **과거 당시에 공개됐던 가격 이력의 동일성**을 보장하지 않는다. Yahoo 비조정 OHLC도 거래소 원시 시세를 보장하지 않는다.
- 해당 공급원이 일시적으로 조회되지 않으면 **정확성 우선으로 보류**하므로, 장마감 후 초기 조회가 느려지거나 일부 정상 신호도 보류될 수 있다.
- KIS 검증 값과 별도로 사용자가 명시적으로 Yahoo 공급원을 고른 경우에는 기존 Yahoo 데이터 분석 경로가 유지된다. 자동 KIS 실패 시 Yahoo 대체도 **검증 실패로 최신 봉을 보류하는 경우**에는 수행하지 않게 방어한다.
- 인증키·비밀번호·Neon 연결정보는 ZIP에 포함되지 않으며, 신규로 설정할 환경변수는 없다.

## API 및 기능

- 자동 검사: `data_sources.kis_data(..., mode='스윙')` → `daily_settlement_guard.verify_latest_kis_daily`.
- 결과 표시: `/api/analysis` 응답 `daily_integrity`; `is_stale`, `final_actionable_signal`에 안전 보류 반영.
- 차트: `/api/chart/history?source=kis&timeframe=1d`의 `daily_integrity`.
- 수동 검사: 인증 필수 `GET /api/research/v5/daily-integrity?symbol=005930.KS`, 5분 결과 캐시. DB 기록이나 주문 없음.
- 기존 가격 비교: `GET /api/research/v5/source-audit`는 그대로 유지.

## 참고

KIS 공식 예제의 [주식현재가 일자별](https://github.com/koreainvestment/open-trading-api/blob/main/examples_llm/domestic_stock/inquire_daily_price/inquire_daily_price.py) (`FHKST01010400`)와 [기간별 차트](https://github.com/koreainvestment/open-trading-api/blob/main/examples_llm/domestic_stock/inquire_daily_itemchartprice/inquire_daily_itemchartprice.py) (`FHKST03010100`). 일부 공식 예제에서 `FID_ORG_ADJ_PRC` 플래그 설명이 일치하지 않아 정확한 수정주가 정의는 배포 후 실제 응답을 검증해야 한다.
