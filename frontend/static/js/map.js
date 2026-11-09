const statusEl = document.getElementById('status');
const startInput = document.getElementById('start');
const endInput = document.getElementById('end');
const windSpeedInput = document.getElementById('windSpeed');
const windDirectionInput = document.getElementById('windDirection');
const showObstaclesInput = document.getElementById('showObstacles');
const showRiskInput = document.getElementById('showRisk');
const missionForm = document.getElementById('missionForm');
const missionTypeInput = document.getElementById('missionType');
const missionStatusEl = document.getElementById('missionStatus');
const createMissionButton = document.getElementById('btnCreateMission');
const gridSpec = window.APP_GRID_SPEC;
const riskThreshold = 0.7;
const gridOriginWgs84 = gridSpec.originWgs84;
const missionWeightDefaults = {
  inspection: [0.2, 0.35, 0.25, 0.2],
  disaster: [0.15, 0.3, 0.4, 0.15],
  delivery: [0.25, 0.25, 0.25, 0.25],
};

if (gridSpec.cesiumIonToken) {
  Cesium.Ion.defaultAccessToken = gridSpec.cesiumIonToken;
}

const viewer = new Cesium.Viewer('viewer', {
  animation: false,
  timeline: false,
  geocoder: false,
  sceneModePicker: false,
  baseLayerPicker: false,
  baseLayer: gridSpec.cesiumIonToken ? undefined : false,
  terrainProvider: new Cesium.EllipsoidTerrainProvider(),
});

viewer.scene.globe.depthTestAgainstTerrain = false;
viewer.camera.flyTo({
  destination: Cesium.Cartesian3.fromDegrees(
    gridOriginWgs84[0],
    gridOriginWgs84[1],
    3000,
  ),
  duration: 0,
});

const origin = Cesium.Cartesian3.fromDegrees(...gridOriginWgs84);
const enuToFixed = Cesium.Transforms.eastNorthUpToFixedFrame(origin);
let routeEntity = null;
let obstacleEntities = [];
let riskEntities = [];
let obstacleCount = 0;
let riskCount = 0;
let currentWindSpeed = Number(windSpeedInput.value);
let currentWindDirection = Number(windDirectionInput.value);

function parsePoint(text) {
  const rawParts = text.split(',').map((value) => value.trim());
  const parts = rawParts.map((value) => Number(value));
  if (
    parts.length !== 3 ||
    rawParts.some((value) => value === '') ||
    parts.some((value) => !Number.isInteger(value)) ||
    parts.some((value, index) => value < 0 || value >= gridSpec.shape[index])
  ) {
    throw new Error('좌표는 격자 범위 안의 정수 x,y,z 형식이어야 합니다.');
  }
  return parts;
}

function parsePoints(text) {
  const trimmed = text.trim();
  return trimmed ? trimmed.split(';').map((point) => parsePoint(point.trim())) : [];
}

function readNumber(id, label) {
  const rawValue = document.getElementById(id).value.trim();
  const value = Number(rawValue);
  if (rawValue === '' || !Number.isFinite(value)) {
    throw new Error(`${label}을(를) 숫자로 입력하세요.`);
  }
  return value;
}

function buildMissionRequest() {
  const missionType = missionTypeInput.value;
  const start = parsePoint(startInput.value);
  const end = parsePoint(endInput.value);
  const waypoints = parsePoints(document.getElementById('waypoints').value);
  const requiredWaypoints = parsePoints(
    document.getElementById('requiredWaypoints').value,
  );
  const costWeights = {
    distance: readNumber('weightDistance', '거리 가중치'),
    risk: readNumber('weightRisk', '위험 가중치'),
    time: readNumber('weightTime', '시간 가중치'),
    energy: readNumber('weightEnergy', '에너지 가중치'),
  };
  const weightValues = Object.values(costWeights);
  if (
    weightValues.some((value) => value < 0 || value > 1) ||
    Math.abs(weightValues.reduce((sum, value) => sum + value, 0) - 1) > 1e-6
  ) {
    throw new Error('비용 가중치는 각각 0~1이며 합계가 1이어야 합니다.');
  }

  const constraints = {
    min_altitude_m: readNumber('minAltitude', '최소 고도'),
    max_altitude_m: readNumber('maxAltitude', '최대 고도'),
    speed_mps: readNumber('speed', '속도'),
    climb_rate_mps: readNumber('climbRate', '상승률'),
    battery_budget_wh: readNumber('batteryBudget', '배터리 예산'),
  };
  if (
    constraints.min_altitude_m < 0 ||
    constraints.max_altitude_m > gridSpec.shape[2] * gridSpec.voxelSizeM ||
    constraints.min_altitude_m >= constraints.max_altitude_m ||
    constraints.speed_mps <= 0 ||
    constraints.climb_rate_mps <= 0 ||
    constraints.climb_rate_mps > constraints.speed_mps ||
    constraints.battery_budget_wh <= 0
  ) {
    throw new Error('고도, 속도, 상승률 또는 배터리 예산 범위를 확인하세요.');
  }
  for (const point of [start, end, ...waypoints]) {
    const altitude = (point[2] + 0.5) * gridSpec.voxelSizeM;
    if (
      altitude < constraints.min_altitude_m ||
      altitude > constraints.max_altitude_m
    ) {
      throw new Error('출발·도착·경유지 고도가 임무 고도 범위 밖입니다.');
    }
  }
  if (
    requiredWaypoints.some(
      (required) => !waypoints.some(
        (waypoint) =>
          waypoint.every((value, index) => value === required[index]),
      ),
    )
  ) {
    throw new Error('필수 경유지는 선택 경유지에도 포함해야 합니다.');
  }

  const payload = {
    mission_type: missionType,
    name: document.getElementById('missionName').value.trim(),
    start,
    end,
    waypoints,
    required_waypoints: requiredWaypoints,
    constraints,
    cost_weights: costWeights,
  };
  if (!payload.name) {
    throw new Error('임무 이름을 입력하세요.');
  }

  if (missionType === 'inspection') {
    payload.target_points = parsePoints(
      document.getElementById('targetPoints').value,
    );
    payload.view_distance_m = readNumber('viewDistance', '시야 거리');
    if (payload.target_points.length === 0 || payload.view_distance_m <= 0) {
      throw new Error('점검 지점과 양수 시야 거리를 입력하세요.');
    }
  } else if (missionType === 'disaster') {
    payload.search_area = parsePoints(
      document.getElementById('searchArea').value,
    );
    payload.priority_points = parsePoints(
      document.getElementById('priorityPoints').value,
    );
    payload.time_limit_s = readNumber('timeLimit', '제한 시간');
    if (payload.search_area.length === 0 || payload.time_limit_s <= 0) {
      throw new Error('탐색 지점과 양수 제한 시간을 입력하세요.');
    }
    if (
      payload.priority_points.some(
        (priority) =>
          !payload.search_area.some((point) =>
            point.every((value, index) => value === priority[index]),
          ),
      )
    ) {
      throw new Error('우선 수색 지점은 탐색 구역에 포함해야 합니다.');
    }
  } else {
    payload.payload_kg = readNumber('payload', '탑재 중량');
    if (payload.payload_kg < 0) {
      throw new Error('탑재 중량은 0 이상이어야 합니다.');
    }
  }
  return payload;
}

function formatApiError(detail) {
  if (Array.isArray(detail)) {
    return detail
      .map((item) => `${item.loc.slice(1).join('.')}: ${item.msg}`)
      .join('; ');
  }
  return detail || '임무 API 요청 실패';
}

function voxelToCartesian([x, y, z]) {
  const local = new Cesium.Cartesian3(
    (x + 0.5) * gridSpec.voxelSizeM,
    (y + 0.5) * gridSpec.voxelSizeM,
    (z + 0.5) * gridSpec.voxelSizeM,
  );
  return Cesium.Matrix4.multiplyByPoint(enuToFixed, local, new Cesium.Cartesian3());
}

function clearRisk() {
  riskEntities.forEach((entity) => viewer.entities.remove(entity));
  riskEntities = [];
}

function updateLayerStatus() {
  statusEl.textContent =
    `장애물 ${obstacleCount} 복셀 · 위험도 ${riskCount} 복셀 · ` +
    `풍속 ${currentWindSpeed} m/s · 풍향 ${currentWindDirection}°`;
}

async function loadEnvironment() {
  const response = await fetch('/api/environment');
  const data = await response.json();
  if (!response.ok) {
    throw new Error(data.detail || '환경 복셀 API 요청 실패');
  }
  if (!Array.isArray(data.blocked_voxels)) {
    throw new Error('환경 복셀 API 응답 형식이 올바르지 않습니다.');
  }

  obstacleEntities.forEach((entity) => viewer.entities.remove(entity));
  obstacleEntities = data.blocked_voxels.map((point) => viewer.entities.add({
    position: voxelToCartesian(point),
    box: {
      dimensions: new Cesium.Cartesian3(
        data.voxel_size_m,
        data.voxel_size_m,
        data.voxel_size_m,
      ),
      material: Cesium.Color.fromCssColorString('#596b7d').withAlpha(0.82),
      outline: true,
      outlineColor: Cesium.Color.fromCssColorString('#243746'),
    },
    show: showObstaclesInput.checked,
  }));
  obstacleCount = obstacleEntities.length;
  updateLayerStatus();
}

async function loadRiskMap() {
  const params = new URLSearchParams({
    wind_speed: windSpeedInput.value,
    wind_direction: windDirectionInput.value,
    threshold: String(riskThreshold),
  });
  const response = await fetch(`/api/risk-map?${params}`);
  const data = await response.json();
  if (!response.ok) {
    throw new Error(data.detail || 'Risk map API 요청 실패');
  }

  clearRisk();
  const lowRiskColor = Cesium.Color.fromCssColorString('#ffd166');
  const highRiskColor = Cesium.Color.fromCssColorString('#d00000');
  for (const point of data.points) {
    riskEntities.push(viewer.entities.add({
      position: voxelToCartesian([point.x, point.y, point.z]),
      box: {
        dimensions: new Cesium.Cartesian3(
          data.voxel_size_m,
          data.voxel_size_m,
          data.voxel_size_m,
        ),
        material: Cesium.Color.lerp(
          lowRiskColor,
          highRiskColor,
          point.risk,
          new Cesium.Color(),
        ).withAlpha(0.35 + point.risk * 0.55),
        outline: false,
      },
      show: showRiskInput.checked,
    }));
  }

  riskCount = riskEntities.length;
  currentWindSpeed = data.wind_speed;
  currentWindDirection = data.wind_direction;
  updateLayerStatus();
}

async function findRoute() {
  const payload = {
    start: parsePoint(startInput.value),
    end: parsePoint(endInput.value),
    wind_speed: Number(windSpeedInput.value),
    wind_direction: Number(windDirectionInput.value),
    vehicle_type: 'passenger',
  };
  if (
    !Number.isFinite(payload.wind_speed) ||
    payload.wind_speed < 0 ||
    payload.wind_speed > 60 ||
    !Number.isFinite(payload.wind_direction) ||
    payload.wind_direction < 0 ||
    payload.wind_direction > 360
  ) {
    throw new Error('풍속은 0~60 m/s, 풍향은 0~360도 범위로 입력하세요.');
  }

  const response = await fetch('/api/route', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  });
  const data = await response.json();
  if (!response.ok) {
    throw new Error(data.detail || 'Route API 요청 실패');
  }

  if (routeEntity) {
    viewer.entities.remove(routeEntity);
  }
  const positions = data.path.map((point) => voxelToCartesian(point));
  routeEntity = viewer.entities.add({
    polyline: {
      positions,
      width: 5,
      material: Cesium.Color.fromCssColorString('#2d6a4f'),
    },
  });

  viewer.zoomTo(routeEntity);
  statusEl.textContent = `Path length=${data.path_length}, avg risk=${data.average_risk.toFixed(3)}, alpha=${data.alpha.toFixed(2)}`;
}

document.getElementById('btnRisk').addEventListener('click', async () => {
  try {
    await loadRiskMap();
  } catch (error) {
    statusEl.textContent = error.message;
  }
});

function updateMissionTypeFields() {
  const missionType = missionTypeInput.value;
  const sections = [
    ['inspectionFields', 'inspection'],
    ['disasterFields', 'disaster'],
    ['deliveryFields', 'delivery'],
  ];
  sections.forEach(([id, type]) => {
    const section = document.getElementById(id);
    const active = missionType === type;
    section.hidden = !active;
    section.querySelectorAll('input').forEach((input) => {
      input.disabled = !active;
    });
  });

  const defaults = missionWeightDefaults[missionType];
  ['weightDistance', 'weightRisk', 'weightTime', 'weightEnergy'].forEach(
    (id, index) => {
      document.getElementById(id).value = defaults[index];
    },
  );
}

missionTypeInput.addEventListener('change', updateMissionTypeFields);
updateMissionTypeFields();

missionForm.addEventListener('submit', async (event) => {
  event.preventDefault();
  if (!missionForm.reportValidity()) {
    return;
  }

  let payload;
  try {
    payload = buildMissionRequest();
  } catch (error) {
    missionStatusEl.textContent = error.message;
    return;
  }

  createMissionButton.disabled = true;
  missionStatusEl.textContent = '임무 저장 중...';
  try {
    const response = await fetch('/api/missions', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    });
    const data = await response.json();
    if (!response.ok) {
      throw new Error(formatApiError(data.detail));
    }

    startInput.value = data.start.join(',');
    endInput.value = data.end.join(',');
    missionStatusEl.textContent =
      `임무 저장 완료 · ${data.name} · ID ${data.id} ` +
      '(메모리 저장: 서버 재시작 시 초기화)';
  } catch (error) {
    missionStatusEl.textContent = `임무 저장 실패: ${error.message}`;
  } finally {
    createMissionButton.disabled = false;
  }
});

showObstaclesInput.addEventListener('change', () => {
  obstacleEntities.forEach((entity) => {
    entity.show = showObstaclesInput.checked;
  });
});

showRiskInput.addEventListener('change', () => {
  riskEntities.forEach((entity) => {
    entity.show = showRiskInput.checked;
  });
});

document.getElementById('btnRoute').addEventListener('click', async () => {
  try {
    await findRoute();
  } catch (error) {
    statusEl.textContent = error.message;
  }
});

Promise.all([loadEnvironment(), loadRiskMap()]).catch((error) => {
  statusEl.textContent = `지도 레이어 로드 실패: ${error.message}`;
});
