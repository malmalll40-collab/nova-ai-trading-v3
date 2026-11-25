# NOVA v4.5 · KIS 일봉/주봉 HTTP 오류 진단 및 단발 재시도 보완

2026-11-21 (KST)

## 운영 증거
- 2026-11-21 21:31~21:37 KST, KIS `1d`·`1w`: 반복 HTTP 502 (`RuntimeError`로 은폐).
- KIS `1mo`: 일부 성공(200)과 일부 실패(502), `1y`: 확인된 요청에서 성공(200).
- KIS 현재가 `/api/market/quote`: 성공(200), Yahoo 차트: 성공(200).
- 기존 DataFrame 캐시 타입 오류는 2026-11-21 배포 코드에서 이미 수정되어 있음.
- 기존 로그는 근본 원인이 HTTP 429/5xx, 네트워크/타임아웃, 응답 파싱 중 무엇인지 구분 불가능.

## 이번 코드 변경
1. `data_sources.py`: KIS 조회 중 HTTP 상태를 보존해서 `KIS_HTTP_429`, `KIS_HTTP_500`, `KIS_HTTP_403` 등 안전한 오류 범주로 변환. JSON 파싱 실패는 `KIS_BAD_JSON`, 연결 오류는 `KIS_UPSTREAM_NETWORK`, 타임아웃은 `KIS_UPSTREAM_TIMEOUT`.
2. KIS GET 요청에서 HTTP 429/500/502/503/504, 연결·타임아웃 오류가 나면 **한 번만** 대기 후 재시도. 영구 4xx와 KIS 비즈니스 오류는 자동 재시도하지 않음. 허위 봉 생성이나 다른 공급원으로의 묵시적 전환 없음.
3. `server.py`: 민감정보 없는 오류 범주를 Render 로그와 인증 후 차트 조회 오류 응답에 표시.
4. 새 테스트 `tests/test_kis_provider_diagnostics.py`: 재시도, 429·403·타임아웃 분류, 민감정보 비노출 회귀 테스트.
5. 기존 추천·신호·매매·월봉·연봉 집계 로직 변경 없음.

## 검증과 미검증
- 로컬 `PYTHONPATH=. pytest -q --disable-warnings --maxfail=1`: **342 passed** (기존 337 + 신규 5).
- 실제 KIS API 인증키로 실서버 조회 및 502 원인별 검증: **아직 수행 불가**.
- 진짜 문제가 KIS 호출 제한, HTTP 서버 오류, 네트워크 오류인 경우 부분적으로 복구 가능. KIS 업무 오류나 다른 런타임 오류라면 추가 수정이 필요함.

## 배포 후 확인
Render 배포가 완료된 후 로그인 상태에서 `/api/chart/history?symbol=005930.KS&source=kis&timeframe=1d` 와 `1w`를 호출. 실패하면 Render Logs에서 `KIS historical candle request failed`를 검색하여 `code=` 항목 확인.
- `KIS_HTTP_429`: 공급원 호출 제한 의심; 호출 빈도·중복조회 최적화 필요.
- `KIS_HTTP_5xx`: KIS 서버 오류/상위 프록시 실패 의심; 재시도 후에도 실패했음.
- `KIS_UPSTREAM_TIMEOUT` 또는 `KIS_UPSTREAM_NETWORK`: 연결 지연/실패.
- `KIS_BAD_JSON`: HTTP 성공이지만 유효한 JSON이 아닌 응답.
- `EGW...` 등 KIS 제공 코드: 해당 제공원 오류 코드를 별도 확인.
- `RuntimeError`만 재등장: 통신 외 위치에서 예외 발생 가능. 안전한 단계별 진단 필요.

배포 전에 GitHub 저장소 루트에 ZIP 파일을 하나만 유지해야 합니다. Render 빌드 명령이 `glob('*.zip')` 결과가 정확히 하나여야 작동합니다. 기존 ZIP은 백업 후 교체하세요.
