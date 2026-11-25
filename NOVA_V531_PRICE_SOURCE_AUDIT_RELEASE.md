# NOVA v5.3.1 — KIS·Yahoo 과거 가격 일치성 연구

- **적용 범위:** 기존 NOVA v5.3 전체 배포 파일 기반. 신규 수동 진단 화면 + 서버 API, 일반 신호·자동추천·Neon 기록 로직 변경 없음.
- **실행:** `검증` → 종목 분석 후 `KIS · Yahoo 가격 비교 실행` 클릭. 요청 경로: `GET /api/research/v5/source-audit?symbol=005930.KS` (인증 필수).
- **공급원:** KIS `/uapi/domestic-stock/v1/quotations/inquire-daily-itemchartprice`의 `FID_ORG_ADJ_PRC=0`(KIS 공식 예제상 수정주가), Yahoo `history(auto_adjust=False)` 기본 OHLC 및 `Adj Close/Close` 비율로 유도한 조정 OHLC. 연구 원본 `yahoo_data`는 별도로 `auto_adjust=True`를 사용함.
- **방법:** 최소 20개 공통 완료 거래일에 한해 동일 날짜 Open/High/Low/Close 절대 상대오차(%), 최대/중앙값과 0.5% 초과 날짜를 집계. 겹치는 날짜 외의 값을 임의 생성하거나 보간하지 않음. 일부 극단적인 가격 불일치는 진단용 경고이며 공식 정답 판정이 아님.
- **진단 단계:** `COMPARED`, `INSUFFICIENT`, `UNAVAILABLE`; `PRICE_BASIS_DIFFERENCE`, `NO_LARGE_DIFFERENCE_OBSERVED`, `INSUFFICIENT_OVERLAP`, `SOURCE_UNAVAILABLE`.
- **제약:** 가격 조정 기준이 같다고 보장하지 않음. `auto_adjust=False`도 거래소 당시 원시 체결자료를 보장하지 않음. 배당·분할 조정 공개 시점 및 실제 체결가는 미검증. 두 시세 공급원을 하나의 시계열로 혼합하거나 비율 보정해 거래 엔진에 반영하지 않음.
- **보안/호출량:** 로그인 인증 및 기존 IP 제한이 적용됨. KIS 인증정보는 서버에만 보관. 수동 조회, 종목당 15분 캐시. 실거래 주문 API 없음. Neon 쓰기 없음.
- **UI:** 시세별 봉 개수, 기준일, 공통 일수, 누락 날짜, OHLC 차이 요약, 최악 차이 날짜와 최근 날짜를 휴대전화에서 확인.
- **회귀:** 신규 6개 테스트, 기존 주요 연구·KIS·달력·자동추적 테스트 일부 통과. 전체 472개 테스트 일괄 실행은 외부 시세를 기다리다 시간 제한으로 중단되어 전체 통과를 주장하지 않음.

## 출처

- KIS 공식 예제(0=수정주가): https://github.com/koreainvestment/open-trading-api/blob/main/legacy/Sample01/kis_domstk.py
- yfinance history 함수: https://ranaroussi.github.io/yfinance/reference/yfinance.price_history.html

## 기존 기능 안전성

- 변경 없음: `engine.py`, `data_sources.py`, `nova_v5_research.py`, `nova_v53_audit.py`, `nova_v52_risk.py`, `nova_v521_aligned.py`, `evidence_store.py`, `auto_evidence.py`, `kis_token_store.py` 등 핵심 계산·저장 모듈.
- 변경: `server.py`(진단 API·버전/출처 메타데이터), `static/index.html`, `static/v5_dashboard.js`, `static/styles.css`, `static/sw.js`, 기존 테스트의 버전 기대값. 신규 `price_source_audit.py`, `tests/test_v531_price_audit.py`.
