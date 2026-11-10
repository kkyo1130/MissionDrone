const statusEl = document.getElementById('status');
const settingsToggle = document.getElementById('toggleSettings');
const settingsPanel = document.getElementById('settingsPanel');
const closeSettingsButton = document.getElementById('closeSettings');
const settingsBackdrop = document.getElementById('settingsBackdrop');
function setSettingsOpen(opened) {
  document.body.classList.toggle('settings-open', opened);
  settingsToggle.setAttribute('aria-expanded', String(opened));
  settingsPanel.setAttribute('aria-hidden', String(!opened));
  if (opened) closeSettingsButton.focus();
}
settingsToggle.addEventListener('click', () => setSettingsOpen(true));
closeSettingsButton.addEventListener('click', () => setSettingsOpen(false));
settingsBackdrop.addEventListener('click', () => setSettingsOpen(false));
document.addEventListener('keydown', (event) => {
  if (event.key === 'Escape' && document.body.classList.contains('settings-open')) {
    setSettingsOpen(false);
    settingsToggle.focus();
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
// Show low-risk voxels too; the route planner always uses the full risk map.
const riskDisplayThreshold = 0.015;
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
  // Keep an actual Cesium globe behind the synthetic test city.
  // OSM imagery does not require an Ion token; fall back to a plain globe
  // when a tile service is unavailable.
  baseLayer: false,
  terrainProvider: new Cesium.EllipsoidTerrainProvider(),
});

viewer.scene.globe.depthTestAgainstTerrain = false;
viewer.scene.globe.show = true;
viewer.scene.globe.baseColor = Cesium.Color.fromCssColorString('#193f62');
viewer.scene.skyAtmosphere.show = true;
viewer.scene.fog.enabled = true;
viewer.scene.globe.enableLighting = false;
// A public imagery layer supplies geographical context without changing
// the local ENU voxel coordinates used by collision checks and route planning.
try {
  const streetTiles = new Cesium.UrlTemplateImageryProvider({
    url: 'https://tile.openstreetmap.org/{z}/{x}/{y}.png',
    credit: '© OpenStreetMap contributors',
    maximumLevel: 19,
  });
  viewer.imageryLayers.addImageryProvider(streetTiles);
  streetTiles.errorEvent.addEventListener((error) => {
    console.warn('Map imagery is unavailable; using the simulated globe.', error);
  });
} catch (error) {
  console.warn('Could not initialise background imagery:', error);
}
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
let currentMissionId = null;
let candidateRoutes = [];
let candidateRouteEntities = [];
const candidateRouteColors = ['#32b7f5', '#b78aff', '#40d4ac', '#ff8c75', '#d7dce8'];
function clearCandidateRouteEntities() {
  for (const entity of candidateRouteEntities) viewer.entities.remove(entity);
  candidateRouteEntities = [];
}
function renderAllCandidateRoutes(selectedIndex = 0) {
  clearCandidateRouteEntities();
  // Draw every candidate simultaneously; the selected route is yellow and wider.
  candidateRoutes.forEach((route, index) => {
    const selected = index === selectedIndex;
    const positions = route.points.map((point) => voxelToCartesian(point));
    candidateRouteEntities.push(viewer.entities.add({
      name: '후보 항로 ' + (index + 1),
      polyline: {
        positions,
        width: selected ? 8 : 4,
        material: Cesium.Color.fromCssColorString(
          selected ? '#ffbd18' : candidateRouteColors[index % candidateRouteColors.length]
        ).withAlpha(selected ? 1 : 0.75),
        clampToGround: false,
      },
    }));
  });
}
function focusRoute(positions) {
  if (!positions.length) return;
  const bounds = Cesium.BoundingSphere.fromPoints(positions);
  viewer.camera.flyToBoundingSphere(bounds, {
    duration: 0.8,
    offset: new Cesium.HeadingPitchRange(-0.8, -0.6, Math.max(bounds.radius * 3.2, 115)),
  });
}
function selectCandidateRoute(index) {
  const route = candidateRoutes[index];
  if (!route) return;
  if (routeEntity) {
    viewer.entities.remove(routeEntity);
    routeEntity = null;
  }
  renderAllCandidateRoutes(index);
  const positions = route.points.map((point) => voxelToCartesian(point));
  selectedRouteEnergyWh = Number(route.estimated_energy_wh);
  focusRoute(positions);
  prepareRoutePlayback(positions);
  setSettingsOpen(false);
  const type = missionTypeInput.value;
  setHudText(type + 'Badge', '항로 생성');
  setHudText(type + 'Detail',
    '선택 후보 ' + (index + 1) + ' · ' + route.distance_m.toFixed(0) + 'm · ' +
    route.estimated_time_s.toFixed(0) + '초 · ' + route.estimated_energy_wh.toFixed(1) + 'Wh');
  setHudWidth(type + 'Bar',
    Number.isFinite(route.average_risk) ? Math.max(4, (1 - route.average_risk) * 100) : 0);
}
const candidateSelect = document.getElementById('candidateSelect');
const candidateSummary = document.getElementById('candidateSummary');
const experimentSummary = document.getElementById('experimentSummary');
const experimentButton = document.getElementById('btnExperiment');
function showRoutePositions(points, detail, risk) {
  clearCandidateRouteEntities();
  if (routeEntity) viewer.entities.remove(routeEntity);
  const positions = points.map((point) => voxelToCartesian(point));
  selectedRouteEnergyWh = null;
  routeEntity = viewer.entities.add({
    polyline: { positions, width: 6, material: Cesium.Color.fromCssColorString('#ffbd18') },
  });
  prepareRoutePlayback(positions);
  focusCity();
  setSettingsOpen(false);
  const type = missionTypeInput.value;
  setHudText(type + 'Badge', '항로 생성');
  setHudText(type + 'Detail', detail);
  setHudWidth(type + 'Bar', Number.isFinite(risk) ? Math.max(4, (1 - risk) * 100) : 0);
}
let obstacleEntities = [];
let cityGroundEntities = [];
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

// The makerthon prototype's rectangular building volumes are now rendered as
// complete Cesium buildings. They come from the same bounds as backend
// OCCUPANCY, so what a drone sees is what the planner avoids.
function localMetersToCartesian(x, y, z) {
  return Cesium.Matrix4.multiplyByPoint(
    enuToFixed, new Cesium.Cartesian3(x, y, z), new Cesium.Cartesian3()
  );
}

function addCityBox(collection, x, y, z, width, depth, height, color, outline = false) {
  const entity = viewer.entities.add({
    position: localMetersToCartesian(x, y, z),
    box: {
      dimensions: new Cesium.Cartesian3(width, depth, height),
      material: Cesium.Color.fromCssColorString(color),
      outline,
      outlineColor: Cesium.Color.fromCssColorString('#142943'),
    },
  });
  collection.push(entity);
  return entity;
}

function renderOsmBuilding(building, index) {
  if (!Array.isArray(building.coordinates) || building.coordinates.length < 3) return;
  const hierarchy = building.coordinates.flatMap((point) => [point[0], point[1]]);
  const color = ['#7796b0', '#8aa5b9', '#6e8a9d', '#a2aeb9'][index % 4];
  const entity = viewer.entities.add({
    name: building.name,
    polygon: {
      hierarchy: Cesium.Cartesian3.fromDegreesArray(hierarchy),
      height: Number(building.min_height_m) || 0,
      extrudedHeight: Number(building.height_m),
      material: Cesium.Color.fromCssColorString(color).withAlpha(0.92),
      outline: true,
      outlineColor: Cesium.Color.fromCssColorString('#273d52'),
    },
    description: 'OSM 건물 · 높이 ' + building.height_m + 'm (' +
      (building.height_source === 'tagged_height' ? 'OSM 높이 태그' : '추정 높이') + ')',
  });
  obstacleEntities.push(entity);
}

async function importOsmStudyArea() {
  const button = document.getElementById('btnOsmBuildings');
  const label = document.getElementById('osmImportStatus');
  button.disabled = true;
  label.textContent = 'OSM 실제 건물 윤곽 조회 및 복셀화 중...';
  try {
    const center_lon = Number(document.getElementById('osmCenterLon').value);
    const center_lat = Number(document.getElementById('osmCenterLat').value);
    const response = await fetch('/api/environment/osm', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({center_lon, center_lat}),
    });
    const result = await response.json();
    if (!response.ok) throw new Error(formatApiError(result.detail));
    label.textContent = 'OSM ' + result.buildings_count + '개 건물 반영 · 높이 추정 ' +
      result.height_estimates + '개 · 화면 새로고침 중';
    window.location.reload();
  } catch (error) {
    label.textContent = '실제 건물 적용 실패: ' + error.message +
      ' (기존 환경 유지)';
    button.disabled = false;
  }
}
document.getElementById('btnOsmBuildings').addEventListener('click', importOsmStudyArea);

async function loadEnvironment() {
  const response = await fetch('/api/environment');
  if (!response.ok) throw new Error('환경 복셀 API 요청 실패 (HTTP ' + response.status + ')');
  const data = await response.json();
  if (!Array.isArray(data.blocked_voxels)) {
    throw new Error('환경 복셀 API 응답 형식이 올바르지 않습니다.');
  }

  obstacleEntities.forEach((entity) => viewer.entities.remove(entity));
  obstacleEntities = [];
  if (data.environment_kind === 'osm_buildings') {
    cityGroundEntities.forEach((entity) => viewer.entities.remove(entity));
    cityGroundEntities = [];
    data.buildings.forEach((building, index) => renderOsmBuilding(building, index));
    const label = document.getElementById('osmImportStatus');
    label.textContent = 'OSM 실건물 ' + data.buildings.length + '개 · 점유 복셀 ' +
      data.blocked_voxels.length + '개 · 미등록 건물/높이 추정 주의';
  } else {
    const label = document.getElementById('osmImportStatus');
    label.textContent = '송도 실제 OSM 건물을 자동으로 불러오는 중입니다. 잠시 기다려 주세요.';
    obstacleCount = 0;
    updateLayerStatus();
    // Fetch after the UI has loaded, rather than blocking FastAPI startup.
    // A failed request keeps the map usable and leaves the manual retry button.
    if (!document.getElementById('btnOsmBuildings').disabled) {
      void importOsmStudyArea();
    }
    return;
  }
  // Real OSM building meshes are the city itself, not optional voxel overlays.
  obstacleEntities.forEach((entity) => { entity.show = true; });
  obstacleCount = data.blocked_voxels.length;
  updateLayerStatus();
}

async function loadRiskMap() {
  const params = new URLSearchParams({
    wind_speed: windSpeedInput.value,
    wind_direction: windDirectionInput.value,
    threshold: String(riskDisplayThreshold),
  });
  const response = await fetch(`/api/risk-map?${params}`);
  const data = await response.json();
  if (!response.ok) {
    throw new Error(data.detail || 'Risk map API 요청 실패');
  }

  clearRisk();
  // Absolute risk scale: green (low) -> yellow (medium) -> red (high).
  // Do not normalize by the displayed voxels' maximum: that would make
  // low-wind conditions look artificially dangerous.
  const lowRiskColor = Cesium.Color.fromCssColorString('#22c55e');
  const mediumRiskColor = Cesium.Color.fromCssColorString('#facc15');
  const highRiskColor = Cesium.Color.fromCssColorString('#ef4444');
  for (const point of data.points) {
    const risk = Math.max(0, Math.min(1, Number(point.risk)));
    const color = risk <= 0.5
      ? Cesium.Color.lerp(lowRiskColor, mediumRiskColor, risk * 2, new Cesium.Color())
      : Cesium.Color.lerp(mediumRiskColor, highRiskColor, (risk - 0.5) * 2, new Cesium.Color());
    riskEntities.push(viewer.entities.add({
      position: voxelToCartesian([point.x, point.y, point.z]),
      box: {
        dimensions: new Cesium.Cartesian3(
          data.voxel_size_m,
          data.voxel_size_m,
          data.voxel_size_m,
        ),
        // Thin overlay: even overlapping low-risk voxels must not hide buildings.
        material: color.withAlpha(0.055 + risk * 0.22),
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
let playbackFrame = null;
let playbackElapsed = 0;
let playbackLastFrame = 0;
let playbackCenter = null;
let playbackPositions = null;
let playbackFinished = false;
let playbackPaused = false;
const startButton = document.getElementById('btnStart');
const resetButton = document.getElementById('btnReset');
const pauseButton = document.getElementById('btnPause');
const playbackProgress = document.getElementById('playbackProgress');
const droneStatusPanel = document.getElementById('droneStatusPanel');
const droneStatusText = document.getElementById('droneStatusText');
const droneBatteryText = document.getElementById('droneBatteryText');
let selectedRouteEnergyWh = null;
let droneHoverTimer = null;
function showDronePanel() {
  if (!droneMarker || !playbackCenter) return;
  clearTimeout(droneHoverTimer);
  droneHoverTimer = null;
  updateDroneStatus();
  const screen = Cesium.SceneTransforms.wgs84ToWindowCoordinates(viewer.scene, playbackCenter);
  if (screen) {
    const bounds = viewer.scene.canvas.getBoundingClientRect();
    const parentBounds = droneStatusPanel.offsetParent.getBoundingClientRect();
    droneStatusPanel.style.left = Math.max(8, Math.min(parentBounds.width - 250, bounds.left - parentBounds.left + screen.x + 22)) + 'px';
    droneStatusPanel.style.top = Math.max(8, Math.min(parentBounds.height - 190, bounds.top - parentBounds.top + screen.y - 60)) + 'px';
  }
  droneStatusPanel.hidden = false;
}
function scheduleDronePanelHide() {
  clearTimeout(droneHoverTimer);
  droneHoverTimer = setTimeout(() => { droneStatusPanel.hidden = true; droneHoverTimer = null; }, 450);
}
droneStatusPanel.addEventListener('mouseenter', () => {
  clearTimeout(droneHoverTimer);
  droneHoverTimer = null;
});
droneStatusPanel.addEventListener('mouseleave', scheduleDronePanelHide);
let droneMarker = null;
let playbackRunning = false;
function updateDroneStatus() {
  const status = playbackFinished ? '도착 완료' : playbackRunning
    ? (playbackPaused ? '일시정지' : '비행 중') : '출발 대기';
  droneStatusText.textContent = `${status} · 진행률 ${Math.round(Math.min(100, playbackElapsed / playbackDurationSeconds * 100))}%`;
  const budget = Number(document.getElementById('batteryBudget').value);
  if (Number.isFinite(selectedRouteEnergyWh) && Number.isFinite(budget) && budget > 0) {
    const used = selectedRouteEnergyWh * Math.min(1, playbackElapsed / playbackDurationSeconds);
    droneBatteryText.textContent = `예상 배터리 ${Math.max(0, 100 - used / budget * 100).toFixed(1)}% · 예상 사용 ${used.toFixed(1)} Wh`;
  } else {
    droneBatteryText.textContent = '예상 배터리: 경로 에너지 정보 없음';
  }
}
function setPlaybackControls(enabled) {
  startButton.disabled = !enabled;
  resetButton.disabled = !enabled;
  pauseButton.disabled = true;
}
startButton.addEventListener('click', () => {
  if (!playbackPositions || playbackRunning) return;
  playbackRunning = true;
  playbackFinished = false;
  playbackPaused = false;
  playbackLastFrame = performance.now();
  startButton.disabled = true;
  pauseButton.disabled = false;
  pauseButton.textContent = 'Ⅱ 일시정지';
  setHudText('routeCaption', '드론 경로 재생 중 · 모의 비행');
  updateDroneStatus();
  playbackFrame = requestAnimationFrame(advanceRoutePlayback);
});
resetButton.addEventListener('click', () => {
  if (playbackPositions) prepareRoutePlayback(playbackPositions);
});
pauseButton.addEventListener('click', () => {
  if (!playbackRunning || playbackFinished) return;
  playbackPaused = !playbackPaused;
  playbackLastFrame = performance.now();
  pauseButton.textContent = playbackPaused ? '▶ 계속 재생' : 'Ⅱ 일시정지';
  updateDroneStatus();
});
viewer.screenSpaceEventHandler.setInputAction((movement) => {
  if (!droneMarker) return;
  const picked = viewer.scene.pick(movement.endPosition);
  if (Cesium.defined(picked) && picked.id === droneMarker) {
    showDronePanel();
  } else if (!droneStatusPanel.hidden && !droneHoverTimer) {
    scheduleDronePanelHide();
  }
}, Cesium.ScreenSpaceEventType.MOUSE_MOVE);
const playbackDurationSeconds = 18;
const droneBodyColor = Cesium.Color.fromCssColorString('#e9f5ff');
const droneAccentColor = Cesium.Color.fromCssColorString('#3f99ff');

function clearRoutePlayback() {
  if (playbackFrame !== null) {
    cancelAnimationFrame(playbackFrame);
    playbackFrame = null;
  }
  if (playbackListener) {
    viewer.clock.onTick.removeEventListener(playbackListener);
    playbackListener = null;
  }
  for (const entity of [...routeMarkers, ...droneParts]) viewer.entities.remove(entity);
  routeMarkers = [];
  droneParts = [];
  droneMarker = null;
  playbackRunning = false;
  clearTimeout(droneHoverTimer);
  droneHoverTimer = null;
  droneStatusPanel.hidden = true;
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

let playbackDistances = [];
let playbackTotalDistance = 0;
function prepareRoutePlayback(positions) {
  clearRoutePlayback();
  if (!positions || !positions.length) return;
  playbackPositions = positions.slice();
  playbackFinished = false;
  playbackPaused = false;
  playbackElapsed = 0;
  setPlaybackControls(true);
  playbackProgress.textContent = '출발 대기 · 0%';
  droneStatusPanel.hidden = true;
  const route = positions.length === 1 ? [positions[0], positions[0]] : positions;
  addRoutePin(route[0], '출발', Cesium.Color.fromCssColorString('#2c88ff'));
  addRoutePin(route[route.length - 1], '도착', Cesium.Color.fromCssColorString('#ffb900'));
  playbackDistances = [0];
  for (let i = 1; i < route.length; i++) {
    playbackDistances.push(playbackDistances[i - 1] + Cesium.Cartesian3.distance(route[i - 1], route[i]));
  }
  playbackTotalDistance = playbackDistances[playbackDistances.length - 1];
  playbackCenter = Cesium.Cartesian3.clone(route[0]);
  droneMarker = viewer.entities.add({
    name: '드론 · 클릭하여 상태 확인',
    position: Cesium.Cartesian3.clone(playbackCenter),
    point: {
      pixelSize: 26, color: Cesium.Color.CYAN,
      outlineColor: Cesium.Color.WHITE, outlineWidth: 4,
      disableDepthTestDistance: Number.POSITIVE_INFINITY,
    },
    label: {
      text: '드론 · 출발 대기',
      font: 'bold 15px sans-serif', fillColor: Cesium.Color.WHITE,
      showBackground: true,
      backgroundColor: Cesium.Color.fromCssColorString('#071329').withAlpha(0.9),
      pixelOffset: new Cesium.Cartesian2(0, -34),
      verticalOrigin: Cesium.VerticalOrigin.BOTTOM,
      disableDepthTestDistance: Number.POSITIVE_INFINITY,
    },
  });
  droneParts.push(droneMarker);
  setHudText('routeCaption', '드론이 출발지에서 대기 중 · 출발 버튼을 눌러주세요');
  updateDroneStatus();
  viewer.scene.requestRender();
}
function advanceRoutePlayback(now) {
  if (!playbackRunning || !droneMarker || !playbackPositions) return;
  if (!playbackPaused) playbackElapsed += Math.max(0, Math.min(0.1, (now - playbackLastFrame) / 1000));
  playbackLastFrame = now;
  const fraction = Math.min(1, playbackElapsed / playbackDurationSeconds);
  const target = fraction * playbackTotalDistance;
  const route = playbackPositions.length === 1 ? [playbackPositions[0], playbackPositions[0]] : playbackPositions;
  let index = 1;
  while (index < playbackDistances.length - 1 && playbackDistances[index] < target) index++;
  const segment = playbackDistances[index] - playbackDistances[index - 1];
  const ratio = segment > 0 ? (target - playbackDistances[index - 1]) / segment : 0;
  Cesium.Cartesian3.lerp(route[index - 1], route[index], ratio, playbackCenter);
  droneMarker.position = Cesium.Cartesian3.clone(playbackCenter);
  if (!droneStatusPanel.hidden) showDronePanel();
  droneMarker.label.text = fraction >= 1 ? '드론 · 도착 완료' : '드론 · 비행 중';
  playbackProgress.textContent = `재생 ${Math.round(fraction * 100)}%`;
  updateDroneStatus();
  viewer.scene.requestRender();
  if (fraction >= 1) {
    playbackFinished = true;
    playbackRunning = false;
    pauseButton.disabled = true;
    playbackFrame = null;
    setHudText('routeCaption', '도착 지점 도달 · 리셋 후 다시 출발할 수 있습니다');
    return;
  }
  playbackFrame = requestAnimationFrame(advanceRoutePlayback);
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

  showRoutePositions(data.path, `항로 ${data.path_length}개 지점 · 평균 위험도 ${data.average_risk.toFixed(2)}`, data.average_risk);
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
    setSettingsOpen(true);
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
    const responseText = await response.text();
    let data;
    try {
      data = JSON.parse(responseText);
    } catch {
      if (!response.ok) {
        throw new Error(`서버 오류 (HTTP ${response.status}). 백엔드 터미널의 오류 로그를 확인해 주세요. ${responseText.slice(0, 120)}`);
      }
      throw new Error('서버가 올바른 JSON 응답을 반환하지 않았습니다.');
    }
    if (!response.ok) {
      throw new Error(formatApiError(data.detail) || `임무 저장 실패 (HTTP ${response.status})`);
    }
    if (!data.id) throw new Error('서버 응답에 임무 ID가 없습니다.');

    currentMissionId = data.id;
    clearCandidateRouteEntities();
    candidateRoutes = [];
    candidateSelect.innerHTML = '<option value="">후보 항로 생성 필요</option>';
    candidateSelect.disabled = true;
    experimentButton.disabled = true;
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
  // This switch must never hide the actual OSM 3D city buildings.
  // Reserved for optional voxel obstacle overlays.
});

showRiskInput.addEventListener('change', () => {
  riskEntities.forEach((entity) => {
    entity.show = showRiskInput.checked;
  });
});


document.getElementById('btnCandidates').addEventListener('click', async () => {
  if (!currentMissionId) {
    candidateSummary.textContent = '먼저 임무를 저장해 주세요.';
    return;
  }
  const button = document.getElementById('btnCandidates');
  button.disabled = true;
  candidateSummary.textContent = '임무 조건에 따른 후보 항로 생성 중...';
  try {
    const response = await fetch(`/api/missions/${currentMissionId}/routes`, {
      method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({
        count: 3,
        wind_speed: Number(windSpeedInput.value),
        wind_direction: Number(windDirectionInput.value),
      }),
    });
    const result = await response.json();
    if (!response.ok) throw new Error(formatApiError(result.detail));
    clearCandidateRouteEntities();
    candidateRoutes = result.routes || [];
    if (!candidateRoutes.length) {
      candidateSelect.innerHTML = '<option value="">생성 가능한 항로 없음</option>';
      candidateSelect.disabled = true;
      experimentButton.disabled = true;
      candidateSummary.textContent = `생성 가능한 후보가 없습니다. ${result.reason || '임무 좌표, 안전거리, 고도, 상승률, 배터리 조건을 확인해 주세요.'}`;
      return;
    }
    candidateSelect.innerHTML = '';
    candidateRoutes.forEach((route, index) => {
      const option = document.createElement('option');
      option.value = String(index);
      option.textContent = `후보 ${index + 1} · ${route.distance_m.toFixed(0)}m · 위험 ${route.average_risk.toFixed(2)} · ${route.estimated_energy_wh.toFixed(1)}Wh`;
      candidateSelect.appendChild(option);
    });
    candidateSelect.disabled = !candidateRoutes.length;
    experimentButton.disabled = !candidateRoutes.length;
    candidateSummary.textContent = `${result.generated_count}개 항로를 동시에 표시 중 · 선택한 항로는 노란색 · ${result.reason || '후보를 선택하면 해당 항로를 재생합니다.'}`;
    if (candidateRoutes.length) candidateSelect.dispatchEvent(new Event('change'));
  } catch (error) {
    candidateSummary.textContent = `후보 생성 실패: ${error.message}`;
    console.error('Candidate route generation failed', error);
  } finally {
    button.disabled = false;
  }
});
candidateSelect.addEventListener('change', () => {
  const route = candidateRoutes[Number(candidateSelect.value)];
  if (!route) return;
  selectCandidateRoute(Number(candidateSelect.value));
});
experimentButton.addEventListener('click', async () => {
  if (!currentMissionId || !candidateRoutes.length) return;
  experimentButton.disabled = true;
  experimentSummary.textContent = activeWeatherProvenance ? '선택한 과거 기상 조건으로 반복 시뮬레이션 중...' : '약풍·중풍·강풍 시나리오를 반복 시뮬레이션하는 중...';
  try {
    const response = await fetch(`/api/missions/${currentMissionId}/experiments`, {
      method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({
        weather: activeWeatherProvenance ? [
          {name: activeWeatherProvenance.source + ' ' + activeWeatherProvenance.timestamp_utc,
           wind_speed_mps: Number(windSpeedInput.value),
           wind_from_deg: Number(windDirectionInput.value)},
        ] : [
          {name:'약풍',wind_speed_mps:2,wind_from_deg:90},
          {name:'중풍',wind_speed_mps:8,wind_from_deg:90},
          {name:'강풍',wind_speed_mps:15,wind_from_deg:90},
        ],
        repeats: 2, seed: 42,
      }),
    });
    const result = await response.json();
    if (!response.ok) throw new Error(formatApiError(result.detail));
    const recommended = result.evaluation?.recommended_route_id;
    const index = candidateRoutes.findIndex((route) => route.id === recommended);
    if (index >= 0) {
      candidateSelect.value = String(index);
      candidateSelect.dispatchEvent(new Event('change'));
    }
    experimentSummary.innerHTML = '';
    const message = document.createElement('div');
    message.textContent = `${result.runs.length}회 모의 비행 저장 · ${recommended ? '안전 조건을 통과한 추천 후보 표시' : '안전 조건을 만족하는 추천 항로 없음'}`;
    const exportLink = document.createElement('a');
    exportLink.href = `/api/experiments/${encodeURIComponent(result.id)}/export?format=csv`;
    exportLink.textContent = ' · CSV 데이터 다운로드';
    exportLink.style.color = '#91c2ff';
    experimentSummary.append(message, exportLink);
  } catch (error) {
    experimentSummary.textContent = `시뮬레이션 실패: ${error.message}`;
  } finally {
    experimentButton.disabled = false;
  }
});

document.getElementById('btnRoute').addEventListener('click', async () => {
  try {
    await findRoute();
  } catch (error) {
    statusEl.textContent = error.message;
  }
});

const historicalWeatherButton = document.getElementById('btnApplyHistoricalWeather');
const historicalWeatherStatus = document.getElementById('historicalWeatherStatus');
let activeWeatherProvenance = null;
historicalWeatherButton.addEventListener('click', async () => {
  historicalWeatherButton.disabled = true;
  historicalWeatherStatus.textContent = '선택한 시간의 기상 데이터를 조회 중...';
  try {
    const at = document.getElementById('historicalWeatherAt').value;
    if (!at) throw new Error('과거 날짜와 시간을 선택해 주세요.');
    const source = document.getElementById('historicalWeatherSource').value;
    // datetime-local is intentionally interpreted as UTC for reproducible historical scenarios.
    const params = new URLSearchParams({at: at + ':00Z', source});
    const response = await fetch('/api/weather/historical?' + params);
    const data = await response.json();
    if (!response.ok) throw new Error(formatApiError(data.detail));
    const speed = Number(data.wind_speed_mps);
    const direction = Number(data.wind_from_deg);
    if (!Number.isFinite(speed) || speed < 0 || speed > 60 ||
        !Number.isFinite(direction) || direction < 0 || direction > 360) {
      throw new Error('기상 데이터의 풍속 또는 풍향이 지원 범위를 벗어났습니다.');
    }
    // Preserve observed/reanalysis precision (e.g. 2.59 m/s); never round to 0.1 m/s.
    windSpeedInput.value = String(speed);
    windDirectionInput.value = String(direction);
    activeWeatherProvenance = data;
    currentWindSpeed = speed;
    currentWindDirection = direction;
    refreshHud();
    await loadRiskMap();
    const correctionText = data.ai_corrected
      ? ' · AI 보정 ' + Number(data.original_wind_speed_mps).toFixed(2) + ' → ' + speed.toFixed(2) + 'm/s (' + data.correction_station + ')'
      : ' · AI 보정 미적용 (학습 모델 없음)';
    historicalWeatherStatus.textContent = data.source + ' · ' + data.timestamp_utc +
      ' · ' + speed.toFixed(2) + 'm/s, ' + direction.toFixed(1) + '°' +
      (source === 'era5' ? correctionText : '') +
      ' · 위험도에 적용 완료 (도시 협곡 실측 아님). 후보 항로를 다시 생성해 주세요.';
  } catch (error) {
    historicalWeatherStatus.textContent = '기상 적용 실패: ' + error.message;
  } finally {
    historicalWeatherButton.disabled = false;
  }
});

windSpeedInput.addEventListener('change', () => {
  activeWeatherProvenance = null;
  const speed = Number(windSpeedInput.value);
  if (Number.isFinite(speed) && speed >= 0 && speed <= 60) {
    currentWindSpeed = speed;
    refreshHud();
  }
});
windDirectionInput.addEventListener('change', () => {
  activeWeatherProvenance = null;
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
