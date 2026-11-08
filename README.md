# MissionDrone

도시 환경·임무 기반 드론 3D 후보 항로 비교 플랫폼. A 담당 P0 백엔드 구현이며 Cesium 프런트엔드는 B 담당입니다.

```bash
python3 -m venv .venv
.venv/bin/pip install -r backend/requirements.lock.txt
# 기본: 로컬 SQLite (외부 서비스 없이 실행)
.venv/bin/uvicorn app.main:app --app-dir backend --reload
```

API 문서: http://127.0.0.1:8000/docs

MySQL 사용:

```bash
docker compose up -d --wait mysql
export DATABASE_URL='mysql+pymysql://missiondrone:missiondrone@127.0.0.1:3306/missiondrone'
.venv/bin/uvicorn app.main:app --app-dir backend --reload
```

`.env.example`은 설정 예시이며 자동으로 로딩하지 않습니다. 환경변수를 export하거나 실행 도구에서 주입하세요. 개발용 테이블은 시작할 때 생성됩니다. 운영 스키마 마이그레이션은 별도 도입이 필요합니다.

테스트 및 데모:

```bash
(cd backend && ../.venv/bin/pytest -q)
# 선택: 별도 테스트용 MySQL DB (테스트 데이터가 남으므로 개발 DB와 분리)
(cd backend && TEST_DATABASE_URL='mysql+pymysql://user:password@127.0.0.1:3306/missiondrone_test' ../.venv/bin/pytest -q)
.venv/bin/python scripts/demo.py
```

데모는 API → 후보 3개 → 기상 3종 × 3회 → DB 저장 → 비교 → 재현 → JSON export를 수행해 `experiments/demo.json`에 저장합니다. 합성 강풍에서는 추천이 없을 수 있습니다.

- [API·좌표·데이터 계약](docs/API_CONTRACT.md)
- [P0 구현·검증 보고서](docs/P0_A_REPORT.md)
- [전체 개발 계획](DEVELOPMENT_PLAN.md)

데이터와 바람은 합성이며 에너지·위험도는 단순 비교 모델입니다. 기존 메이커톤 코드, 실제 도시/과거 기상 데이터는 제공되지 않았습니다.
