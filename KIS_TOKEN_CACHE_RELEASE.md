# NOVA v4.5.1 — KIS OpenAPI 재발급 알림 감소 개선

- 신규 `kis_token_store.py`: PostgreSQL `DATABASE_URL`로 암호화된 KIS OAuth 토큰을 유지합니다. 서버가 재시작하거나 무료 Render가 절전 후 깨어나도 유효 토큰을 재사용합니다.
- `data_sources.py`: 프로세스 내 단일 잠금 범위를 발급 과정까지 확장하여 중복발급을 방지합니다. 연결된 PostgreSQL에서는 프로세스 간 트랜잭션 advisory lock으로 발급을 직렬화합니다.
- 토큰은 데이터베이스에 **AES-256-GCM 암호문**으로 저장하며, 기존 Render의 `KIS_APP_KEY`와 `KIS_APP_SECRET`에서 분리된 암호화 키를 유도합니다. 키와 평문 토큰을 GitHub, 로그, DB에 저장하지 않습니다.
- `DATABASE_URL`이 없는 로컬 환경은 기존처럼 메모리 캐시를 사용합니다. `DATABASE_URL`이 설정되었는데 DB가 작동하지 않을 때는 불필요한 카카오 알림과 발급제한을 막기 위해 새 토큰을 발급하지 않습니다. 이 경우 KIS 조회가 실패할 수 있습니다.
- 변경 범위: `data_sources.py`, `requirements.txt` + 신규 `kis_token_store.py`, `tests/test_kis_token_persistence.py`, 이 안내. **주문/종목 추천/분석/프런트 UI는 미변경**.

## 적용 순서

1. Neon 공식 사이트에서 Free PostgreSQL 프로젝트를 생성하고 연결 문자열을 복사합니다. 반드시 SSL(`sslmode=require`)을 사용하세요. 무료 한도 및 요금제를 확인하세요.
2. Render 웹 서비스 > Environment에서 `DATABASE_URL`에 **Neon 외부 접속 문자열**을 저장합니다. `KIS_APP_KEY`, `KIS_APP_SECRET`은 기존 값을 유지하세요. 연결 문자열은 채팅, GitHub에 공개하지 마세요.
3. 기존 배포 ZIP을 이 ZIP 하나로 교체하여 GitHub main에 커밋하고, Render 자동 배포가 Live로 변경되는지 확인합니다. 기존 ZIP은 히스토리에서 복구할 수 있습니다. 루트 ZIP은 정확히 1개여야 합니다.
4. 처음 KIS 시세를 호출했을 때 토큰 알림이 1회 올 수 있습니다. Render가 15분 유휴 후 절전되어 다시 실행되더라도, 토큰이 유효한 동안에는 새 알림이 오지 않는지 확인합니다. 24시간 내외 만료 시 정상 재발급 알림은 올 수 있습니다.

## 보안 및 운영

- 새 PostgreSQL 저장소는 KIS OAuth 캐시와 기존 신호 기록이 같은 `DATABASE_URL`을 공유할 수 있습니다(서로 다른 테이블).
- 연결 문제가 생기면 KIS 조회 실패를 먼저 점검하고 무작정 토큰을 재발급하지 마세요.
- 비밀키와 데이터베이스 URL은 서버의 Secret/Environment에만 보관하십시오.
- Render 무료 서비스 첫 접속 지연은 해결하지 않습니다.
- 실거래 시세 응답과 실제 Render 배포는 별도로 확인해야 합니다.
