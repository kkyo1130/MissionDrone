# B 연동 계약 — P0 v1

전체 요청/응답 스키마는 실행 중인 `/docs`, `/openapi.json`에서 확인한다. 입력의 미정의 필드, NaN/Infinity, 범위 밖 수치는 422로 거절한다. 없는 ID는 404, 후보 없이 실험 실행은 409다. 실패 항로 생성은 200 + 빈 routes + reason으로 응답한다.

## 좌표·환경

내부 좌표는 미터 단위 로컬 ENU: x 동쪽, y 북쪽, z 원점 위 높이. 격자 노드는 resolution 간격이며 정확한 출발·도착·경유 좌표도 탐색 축에 포함한다. 환경 size 밖과 임무 고도 범위 밖은 통과하지 않는다. 고도 제약은 ENU z 기준이다.

환경 origin_lat/lon은 WGS84 도, origin_alt는 타원체고(m). API의 wgs84는 WGS84 ECEF/ENU 회전을 거쳐 변환한 longitude/latitude/altitude이며 Cesium Cartesian3.fromDegrees에 전달한다. 지면고도·지오이드 보정·실제 지형은 포함하지 않는다.

장애물과 금지 구역은 닫힌 AABB다. 경로 선분 전체에 대해 박스와의 최소 유클리드 거리를 계산한다. 허용 거리는 clearance_m + drone_radius_m + position_error_m보다 커야 한다. 경계 접촉도 차단한다. 회전각은 연속 선분 방향 변화, 상승률은 수직속도 절댓값으로 검사한다. 기상 반영 후 상승률을 다시 검사한다.

위험도는 0~1이며 risk + uncertainty를 1로 제한한 보수적 값이다. 공간상 겹치는 위험 필드는 최대값을 사용한다. 위험 필드는 비용·노출에 반영되며, 통행 금지는 no_fly_zones로 지정한다. 자동으로 위험 영역을 금지 영역으로 변환하지 않는다.

`GET /api/environments/{id}/voxels?offset=0&limit=1000`은 박스/위험 필드에서 계산한 점유·금지·위험 격자 중심을 페이지로 반환한다. 셀 중심의 점유 표현과 별도로 탐색은 원래 박스 기하를 검사하므로 복셀 모서리 관통을 허용하지 않는다. 이 API는 위험 시각화용이고 안전 이격 영역 표시는 원본 박스와 임무 여유 거리를 사용한다.

## 기본 흐름

1. `GET /api/environments`: 기본 합성 환경 `demo-city`. 사용자 환경 등록은 `POST /api/environments`.
2. `POST /api/missions`: 예시는 `data/samples/delivery.json`. kind는 delivery / inspection / disaster. 점검·재난은 비어 있지 않은 targets가 필요하다. 모든 임무에서 waypoints는 지정 순서 필수 방문이며, 점검·재난은 그 뒤 targets를 지정 순서대로 필수 방문한다. 배송 targets는 관측 커버리지 평가에만 사용한다.
3. `POST /api/missions/{id}/routes`, body `{"count":3}`: 3~5개 요청. 안전·비행 제약과 유사도 제거를 통과한 실제 개수, reason, 경로별 points / wgs84 / distance_m / risk_score(경로 노드의 최대 보수적 위험값)를 반환한다. 대체 경로 생성은 누적 경로 패널티 + 위험 가중치 변화 방식으로, 완전한 K-shortest 보장은 없다. 요청당 최대 count×4회, 각 구간 최대 100000 상태를 확장한다. 제약/탐색 예산 때문에 후보 수가 부족할 수 있다.
4. `GET /api/missions/{id}/routes`: 최신 생성 묶음만 반환한다. 과거 실험은 당시 경로 스냅샷을 유지한다.
5. `GET /api/weather-scenarios`: 기본 weak/moderate/strong, 모두 합성. 추가는 `POST /api/weather-scenarios`. wind_from_deg는 기상학적 불어오는 방향(북=0°, 동=90°), wind_speed_mps는 m/s. timestamp는 타임존 포함 ISO 8601이며 source 필수. historical=true는 사용자가 검증한 과거 관측을 등록할 때만 쓴다.
6. `POST /api/simulations`, body `{"mission_id":"…","weather_ids":["weak","moderate","strong"],"repeats":3,"seed":42}`. 동기 실행 후 완료한 실험과 runs를 반환한다. scenario×route×repeat마다 seed+repeat를 기록한다. 같은 반복의 바람 교란은 후보 간 공통으로 적용한다.
7. `POST /api/evaluations/compare`, body `{"experiment_id":"…"}`. 전체 선택 기상/반복을 모두 통과한 후보만 종합 추천한다. scenario_results는 기상별 추천이다. 추천 불가 시 recommended_route_id=null. scores는 높을수록 좋다.
8. `GET /api/experiments/{id}`, `/api/simulations/{id}`: 설정·입력 스냅샷·실험·평가 조회. `/api/experiments/{id}/export?format=json|csv` 내보내기.
9. `POST /api/experiments/{id}/replay`: 스냅샷+seed로 재계산 후 원본 metrics와 정확히 비교. 버전/코드 해시가 다르면 409. 저장 데이터는 변경하지 않는다.

## 지표와 추천

uniform-wind 모델에서 횡풍을 상쇄할 수 있는 대기속도와 순풍 성분으로 지상속도를 구한다. 풍속에는 seed 기반 ±5% 교란을 준다. 지상속도 ≤0.5m/s 또는 횡풍 상쇄 불가는 실패다. 시간·에너지는 유효하게 진행한 구간의 누적 값이므로 safe=false이면 도착 지표로 사용하지 않는다.

에너지(Wh)는 `(180 + 25*payload_kg + 4*wind_speed² + 100*상승방향성분) W × 시간`의 단순 모델이다. 위험 노출(s)은 위험값에 바람 보정값을 더한 시간 적분 추정이다. risk_voxel_ratio는 위험값 >0.2인 구간의 거리 비율을 세부 샘플 평균으로 추정하며 바람 보정은 제외한다. 커버리지는 targets에서 observation_radius_m 내 경로 노드가 존재하는 비율이고, 점검 시야 차폐/재난 수색 면적 커버리지를 모델링하지 않는다.

safe는 경로 기하·회전·상승률, 바람 비행가능성, 배터리·시간, 타깃 커버리지 통과를 뜻한다. 실제 비행 안전 인증을 뜻하지 않는다. minimum_clearance_m은 장애물+금지구역까지의 선분 최소 거리로, 해당 박스가 없으면 null이다.

후보별 반복·기상 평균을 취한 뒤 안전 후보 사이 min-max 정규화한다. 동일 값은 0 비용. 거리/시간/에너지/위험 비용 가중치는 배송 .2/.4/.3/.1, 점검 .1/.2/.2/.5, 재난 .1/.5/.1/.3이다. 점검/재난은 필수 타깃 방문과 100% 커버리지가 추천의 선행 조건이다.

## 저장·재현

SQLAlchemy로 environments, missions, weather_scenarios, route_generations, route_candidates, experiment_batches, simulation_runs, evaluation_results를 관리한다. 관계는 외래키, 상세 설정/배열은 JSON이다. 실험 한 묶음은 트랜잭션으로 저장하며 입력 SHA256, 백엔드 코드 SHA256, 시뮬레이터 버전, seed, 데이터 종류, 단위, 원본 환경·임무·후보·기상을 저장한다. P0의 작은 격자는 환경 박스에서 유도하며 대형 파일/URI 저장은 구현하지 않았다.

범위 제한: 환경 기본 격자 최대 150000 노드, 경유지 20개, 타깃 30개, 기상 10개, 반복 30회. 요청은 동기식이며 장시간 대형 실험의 작업 큐·인증·운영 마이그레이션은 후속 작업이다.
