# NOVA v4.7.1 시장 상황별 연구 결과 버튼 보완

## 운영 장애 관련 확인
- Render v4.7.0 배포 자체는 Live.
- 동일 시간대 `/static/journal.js` 200 응답 기록 있음.
- 스크린샷 확인 시간대 `/api/research/market-regimes` 요청은 Render에서 확인되지 않음.
- 이 증거만으로 클릭 핸들러 누락, 브라우저 PWA 캐시, 실제 사용자 클릭 여부 중 어느 하나를 단정할 수 없음.

## 범위
- `static/journal.js`: 앱 초기화 실패 시에도 버튼을 독립적으로 연결. 백테스트 메뉴 첫 진입에서 연구 결과를 1회 조회. 수동 재조회 가능. 준비/로딩/오류/표본 부족 상태를 표시.
- `static/index.html`: v4.7.1 화면 버전 표시.
- `static/sw.js`: PWA 캐시 버전 변경.
- `server.py`: `/api/health` 버전 표시와 동일한 앱 버전 필드 `4.7.1` 변경.
- 기존 KIS/완료봉/매수매도 판단/Neon DB/자동 추천 기록/자동 예약 기능 변경 없음.

## 확인
- JavaScript `node --check` 통과.
- Python `py_compile` 통과.
- 브라우저 모의 테스트: 독립 초기화 시 버튼이 서버 API를 호출하고 결과를 표시, 재조회 가능.
- 원본 v4.7와 비교해 운영 핵심 137개 파일 바이트 동일(비코드 캐시 제외).
- **전체 Python 회귀 테스트는 현재 작업 환경에 `yfinance`, `psycopg`가 없어 실행하지 못함.**
- 실제 휴대전화·Render 운영 서버 검증은 배포 후 진행.

## 배포
1. GitHub 저장소 최상위 ZIP 하나 원칙을 지켜 v4.7 ZIP을 이 ZIP으로 교체.
2. 기존 `.github` 폴더(워크플로·스크립트) 그대로 보존.
3. Render 자동 배포가 Live가 되면 NOVA 앱/브라우저를 종료 후 다시 실행.
4. 과거 검증에 들어가면 '조회 중…'을 거쳐 결과가 표시되는지 확인.
5. 버튼을 누를 때 `/api/research/market-regimes` 요청이 Render 로그에 나타나고 200인지 확인. 서버 요청 401/503이면 별도 원인 진단.

환경변수 `DATABASE_URL`, `NOVA_AUTOTRACK_TOKEN`, KIS 키는 변경하지 않음. 기존 데이터 삭제나 마이그레이션 없음.
