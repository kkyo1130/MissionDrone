# MissionDrone

도시 환경·임무 기반 AI 드론 3D 항로 최적화 플랫폼입니다. 현재는 원본 메이커톤 프로토타입을 이관한 F00 기반 단계이며, 3D 복셀 mock 환경에서 위험도와 단일 A* 경로를 확인할 수 있습니다.

## 빠른 시작

요구 사항: Python 3.10 이상. MySQL을 실행하려면 Docker Compose가 추가로 필요합니다.

```powershell
Copy-Item .env.example .env
cd backend
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e ".[dev]"
python -m uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
```

브라우저에서 `http://127.0.0.1:8000`을 열고, `http://127.0.0.1:8000/docs`에서 API를 확인할 수 있습니다. 테스트는 `backend/`에서 `python -m pytest`로 실행합니다.

### MySQL 로컬 서비스

`.env`의 MySQL 로컬 개발용 값(기본값은 예시용)을 확인한 뒤 저장소 루트에서 실행합니다.

```powershell
docker compose up -d mysql
```

F00에서는 MySQL 서비스 설정까지만 제공하며, API의 영속 저장소 연동과 스키마는 F07 범위입니다.

## 현재 범위와 데이터

- 원본의 32×32×12 mock 복셀 환경, 위험도 휴리스틱, 26방향 3D A*, CesiumJS 시각화를 이관했습니다.
- 실제 송도 건물·기상 데이터는 원본 저장소에 없었습니다. 현재 결과는 mock 환경 기반이며 실환경 안전성이나 정확성을 의미하지 않습니다.
- 복셀 인덱스는 `(x, y, z)`, 각 축 단위는 `voxel_size_m` 미터입니다. Cesium 화면은 grid 원점을 WGS84 `(126.62°E, 37.37°N, 0 m)`로 두고 ENU 좌표계에서 복셀 중심을 표시합니다. 이는 데모용 원점이며 실제 도시 데이터와 정합된 좌표가 아닙니다.
- `CESIUM_ION_TOKEN`은 선택 사항입니다. Cesium Ion을 사용할 경우 공개 클라이언트 토큰을 도메인/자산 제한 후 설정하세요.

## 프로젝트 구조

```text
backend/
├── app/
│   ├── core/                 # 복셀, 위험도, 경로 탐색, 가중치
│   └── main.py               # FastAPI 앱 및 API
├── tests/
└── pyproject.toml
frontend/
├── static/js/map.js
└── templates/index.html
data/
└── README.md                # 데이터 출처 및 라이선스 기록 기준
docs/
└── F00-MIGRATION.md          # 이관 출처, 라이선스 및 데이터 확인 기록
docker-compose.yml            # 로컬 MySQL 서비스
```

## API

- `GET /health`: 서버 상태
- `GET /api/risk-map`: 바람 조건에 따른 고위험 복셀
- `POST /api/route`: 시작/도착 복셀 사이의 단일 경로

경로 요청 예시:

```json
{
  "start": [2, 2, 6],
  "end": [28, 28, 6],
  "wind_speed": 8,
  "wind_direction": 90,
  "vehicle_type": "passenger"
}
```

## 라이선스 및 외부 자산

원본 코드의 출처·권한 확인은 [F00 이관 기록](docs/F00-MIGRATION.md)을 참고하세요. 원본에 LICENSE 파일이 없어 이 저장소에도 별도 라이선스를 추가하지 않았습니다. CesiumJS는 CDN으로 불러오며 이 저장소에 라이브러리 파일을 복제하지 않습니다.
