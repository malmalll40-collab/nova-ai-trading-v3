# NOVA v4.5 한국시장 전용 변경 (2026-11-12)

- 삭제: 나스닥 회사명 검색, 주요 나스닥 종목 관심 후보 스캔, 미국주식 빠른 선택 버튼 및 해당 소스 모듈.
- 기존 저장된 미국주식 티커는 앱 시작 시 분석창과 관심목록에서 제외하고, 기본 삼성전자 종목코드로 전환.
- /api/search?market=nasdaq, /api/recommendations/run market=nasdaq 또는 미국주식 제공원은 HTTP 422 거절.
- /api/analysis, /api/chart/history, /api/watch/scan 은 국내 거래소 접미사 .KS/.KQ를 요구하며 데모는 유지.
- 코스피·코스닥 시세 및 추천, 네이버 수급, 손절·목표 연구, 13개 역할의 의사결정은 변경하지 않음.
- 개별 미국주식 조회가 필요하면 별도 버전에서만 지원하며 본 배포의 공개 API에서 비활성화.
- 이 배포는 GitHub/Render 자동 적용되지 않으며 ZIP을 교체해 배포해야 함.
