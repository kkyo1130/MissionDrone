const statusEl = document.getElementById('status');
const settingsToggle = document.getElementById('toggleSettings');
settingsToggle.addEventListener('click', () => {
  const opened = document.body.classList.toggle('settings-open');
  settingsToggle.setAttribute('aria-expanded', String(opened));
  settingsToggle.textContent = opened ? '✕ 설정 닫기' : '⚙ 임무 설정';
});
document.addEventListener('keydown', (event) => {
  if (event.key === 'Escape' && document.body.classList.contains('settings-open')) {
    settingsToggle.click();
  }
});
function setHudText(id, value) {
  const element = document.getElementById(id);
  if (element) element.textContent = value;
}
function setHudWidth(id, percentage) {
  const element = document.getElementById(id);
  if (element) element.style.width = `${Math.max(0, Math.min(100, percentage))}%`;
}
function refreshHud() {
  setHudText('hudWind', currentWindSpeed.toFixed(1));
  setHudText('hudDirection', String(currentWindDirection));
  setHudText('hudWindMetric', `${currentWindSpeed.toFixed(1)} m/s`);
  setHudText('hudObstacleCount', `${obstacleCount.toLocaleString()}개`);
  setHudText('hudRiskCount', `${riskCount.toLocaleString()}개`);
  setHudText('hudRiskMetric', `${riskCount.toLocaleString()}개`);
  setHudWidth('hudWindBar', currentWindSpeed / 60 * 100);
  setHudWidth('hudRiskBar', obstacleCount + riskCount ? riskCount / (obstacleCount + riskCount) * 100 : 0);
}

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
// Frame the actual simulated city instead of viewing it from 3 km away.
const cityCenterLocal = new Cesium.Cartesian3(
  gridSpec.shape[0] * gridSpec.voxelSizeM / 2,
  gridSpec.shape[1] * gridSpec.voxelSizeM / 2,
  gridSpec.shape[2] * gridSpec.voxelSizeM / 3,
);
const cityOrigin = Cesium.Cartesian3.fromDegrees(...gridOriginWgs84);
const cityTransform = Cesium.Transforms.eastNorthUpToFixedFrame(cityOrigin);
const cityCenter = Cesium.Matrix4.multiplyByPoint(cityTransform, cityCenterLocal, new Cesium.Cartesian3());
const cityRadius = Math.hypot(
  gridSpec.shape[0] * gridSpec.voxelSizeM / 2,
  gridSpec.shape[1] * gridSpec.voxelSizeM / 2,
  gridSpec.shape[2] * gridSpec.voxelSizeM / 2,
);
const cityBounds = new Cesium.BoundingSphere(cityCenter, cityRadius);
function focusCity() {
  viewer.camera.flyToBoundingSphere(cityBounds, {
    duration: 0.8,
    offset: new Cesium.HeadingPitchRange(-0.8, -0.65, cityRadius * 2.15),
  });
}
focusCity();
document.getElementById('btnFocus').addEventListener('click', focusCity);

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
  refreshHud();
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


/* Additive Cesium route playback. The referenced makerthon prototype uses a
   polyline and camera zoom; this extends that pattern with start/end markers
   and a lightweight drone silhouette without changing mission/UI contracts. */
let routeMarkers = [];
let droneParts = [];
let playbackListener = null;
let playbackStartedAt = 0;
let playbackPositions = null;
let playbackFinished = false;
const replayButton = document.getElementById('btnReplay');
const pauseButton = document.getElementById('btnPause');
const playbackProgress = document.getElementById('playbackProgress');
function setPlaybackControls(enabled) {
  replayButton.disabled = !enabled;
  pauseButton.disabled = !enabled;
}
pauseButton.addEventListener('click', () => {
  if (!playbackPositions || playbackFinished) return;
  viewer.clock.shouldAnimate = !viewer.clock.shouldAnimate;
  pauseButton.textContent = viewer.clock.shouldAnimate ? 'Ⅱ 일시정지' : '▶ 계속 재생';
});
replayButton.addEventListener('click', () => {
  if (playbackPositions) startRoutePlayback(playbackPositions);
});
const playbackDurationSeconds = 18;
const droneBodyColor = Cesium.Color.fromCssColorString('#e9f5ff');
const droneAccentColor = Cesium.Color.fromCssColorString('#3f99ff');

function clearRoutePlayback() {
  if (playbackListener) {
    viewer.clock.onTick.removeEventListener(playbackListener);
    playbackListener = null;
  }
  for (const entity of [...routeMarkers, ...droneParts]) viewer.entities.remove(entity);
  routeMarkers = [];
  droneParts = [];
}

function addRoutePin(position, text, color) {
  const pin = viewer.entities.add({
    position,
    point: { pixelSize: 13, color, outlineColor: Cesium.Color.WHITE, outlineWidth: 2, disableDepthTestDistance: Number.POSITIVE_INFINITY },
    label: {
      text, font: 'bold 14px sans-serif', fillColor: Cesium.Color.WHITE,
      showBackground: true, backgroundColor: Cesium.Color.fromCssColorString('#0a1a36').withAlpha(0.92),
      backgroundPadding: new Cesium.Cartesian2(10, 7),
      pixelOffset: new Cesium.Cartesian2(0, -29),
      verticalOrigin: Cesium.VerticalOrigin.BOTTOM,
      disableDepthTestDistance: Number.POSITIVE_INFINITY,
    },
  });
  routeMarkers.push(pin);
}

function makeDrone(positionProperty) {
  // Drone is assembled from native Cesium shapes, requiring no external 3D asset.
  // Body and four rotors follow the same animated center position.
  const offsets = [
    [0, 0, 0], [-2.4, -2.4, 0.2], [-2.4, 2.4, 0.2],
    [2.4, -2.4, 0.2], [2.4, 2.4, 0.2],
  ];
  offsets.forEach(([dx, dy, dz], index) => {
    const offsetPosition = new Cesium.CallbackProperty((time) => {
      const center = positionProperty.getValue(time);
      if (!center) return undefined;
      const local = new Cesium.Cartesian3(dx, dy, dz);
      const transform = Cesium.Transforms.eastNorthUpToFixedFrame(center);
      return Cesium.Matrix4.multiplyByPoint(transform, local, new Cesium.Cartesian3());
    }, false);
    droneParts.push(viewer.entities.add({
      position: offsetPosition,
      ellipsoid: {
        radii: index === 0
          ? new Cesium.Cartesian3(2.6, 1.8, 0.8)
          : new Cesium.Cartesian3(1.6, 1.6, 0.15),
        material: index === 0 ? droneBodyColor : droneAccentColor,
        outline: true,
        outlineColor: Cesium.Color.fromCssColorString('#142f57'),
      },
    }));
  });
  // Arms form the characteristic quadcopter cross.
  for (const [dx, dy] of [[-2.4, -2.4], [-2.4, 2.4], [2.4, -2.4], [2.4, 2.4]]) {
    droneParts.push(viewer.entities.add({
      polyline: {
        positions: new Cesium.CallbackProperty((time) => {
          const center = positionProperty.getValue(time);
          if (!center) return [];
          const transform = Cesium.Transforms.eastNorthUpToFixedFrame(center);
          const end = Cesium.Matrix4.multiplyByPoint(transform, new Cesium.Cartesian3(dx, dy, 0), new Cesium.Cartesian3());
          return [center, end];
        }, false),
        width: 4, material: droneBodyColor,
      },
    }));
  }
}

function startRoutePlayback(positions) {
  clearRoutePlayback();
  if (!positions.length) return;
  playbackPositions = positions.slice();
  playbackFinished = false;
  setPlaybackControls(true);
  pauseButton.textContent = 'Ⅱ 일시정지';
  playbackProgress.textContent = '0%';
  if (positions.length === 1) positions = [positions[0], positions[0]];
  addRoutePin(positions[0], '출발', Cesium.Color.fromCssColorString('#2c88ff'));
  addRoutePin(positions[positions.length - 1], '도착', Cesium.Color.fromCssColorString('#ffb900'));

  // Distance-weighted interpolation keeps the drone moving along every route segment.
  const distances = [0];
  for (let i = 1; i < positions.length; i++) {
    distances.push(distances[i - 1] + Cesium.Cartesian3.distance(positions[i - 1], positions[i]));
  }
  const totalDistance = distances[distances.length - 1];
  const currentPosition = new Cesium.Cartesian3();
  const dronePosition = new Cesium.CallbackPositionProperty((time, result) => {
    const elapsed = Math.max(0, Cesium.JulianDate.secondsDifference(time, playbackStartedAt));
    const target = Math.min(1, elapsed / playbackDurationSeconds) * totalDistance;
    let index = 1;
    while (index < distances.length - 1 && distances[index] < target) index++;
    const segment = distances[index] - distances[index - 1];
    const ratio = segment > 0 ? (target - distances[index - 1]) / segment : 0;
    return Cesium.Cartesian3.lerp(positions[index - 1], positions[index], ratio, result || currentPosition);
  }, false);
  playbackStartedAt = Cesium.JulianDate.clone(viewer.clock.currentTime);
  viewer.clock.shouldAnimate = true;
  makeDrone(dronePosition);
  setHudText('routeCaption', '드론 경로 재생 중 · 모의 비행');
  playbackListener = (clock) => {
    const elapsed = Cesium.JulianDate.secondsDifference(clock.currentTime, playbackStartedAt);
    const progress = Math.min(100, Math.max(0, Math.round(elapsed / playbackDurationSeconds * 100)));
    playbackProgress.textContent = `${progress}%`;
    if (elapsed >= playbackDurationSeconds) {
      viewer.clock.onTick.removeEventListener(playbackListener);
      playbackListener = null;
      playbackFinished = true;
      pauseButton.disabled = true;
      setHudText('routeCaption', '도착 지점 도달 · 모의 비행 완료');
    }
  };
  viewer.clock.onTick.addEventListener(playbackListener);
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
      material: Cesium.Color.fromCssColorString('#ffbd18'),
    },
  });

  startRoutePlayback(positions);
  // Keep the city and both endpoints in view throughout the playback.
  focusCity();
  document.body.classList.remove('settings-open');
  settingsToggle.setAttribute('aria-expanded', 'false');
  settingsToggle.textContent = '⚙ 임무 설정';
  const type = missionTypeInput.value;
  const label = { inspection: '시설 점검', disaster: '재난 대응', delivery: '물류 배송' }[type];
  const detail = `항로 ${data.path_length}개 지점 · 평균 위험도 ${data.average_risk.toFixed(2)}`;
  setHudText(type + 'Badge', '탐색 완료');
  setHudText(type + 'Detail', detail);
  setHudWidth(type + 'Bar', Math.max(4, (1 - data.average_risk) * 100));
  // The playback status is maintained by the Cesium clock listener.
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
  document.querySelectorAll('.mission-choice').forEach((button) => {
    const active = button.dataset.mission === missionType;
    button.classList.toggle('selected', active);
    button.setAttribute('aria-pressed', String(active));
  });
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
document.querySelectorAll('.mission-choice').forEach((button) => {
  button.addEventListener('click', () => {
    missionTypeInput.value = button.dataset.mission;
    updateMissionTypeFields();
    document.body.classList.add('settings-open');
    settingsToggle.setAttribute('aria-expanded', 'true');
    settingsToggle.textContent = '✕ 설정 닫기';
  });
});
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
    missionStatusEl.textContent = `임무 저장 완료 · ${data.name} · ID ${data.id} (DB 저장)`;
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

windSpeedInput.addEventListener('change', () => {
  const speed = Number(windSpeedInput.value);
  if (Number.isFinite(speed) && speed >= 0 && speed <= 60) {
    currentWindSpeed = speed;
    refreshHud();
  }
});
windDirectionInput.addEventListener('change', () => {
  const direction = Number(windDirectionInput.value);
  if (Number.isFinite(direction) && direction >= 0 && direction <= 360) {
    currentWindDirection = direction;
    refreshHud();
  }
});
refreshHud();

Promise.all([loadEnvironment(), loadRiskMap()]).catch((error) => {
  statusEl.textContent = `지도 레이어 로드 실패: ${error.message}`;
});
