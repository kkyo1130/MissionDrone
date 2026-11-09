const statusEl = document.getElementById('status');
const startInput = document.getElementById('start');
const endInput = document.getElementById('end');
const windSpeedInput = document.getElementById('windSpeed');
const windDirectionInput = document.getElementById('windDirection');
const showObstaclesInput = document.getElementById('showObstacles');
const showRiskInput = document.getElementById('showRisk');
const gridSpec = window.APP_GRID_SPEC;
const riskThreshold = 0.7;
const gridOriginWgs84 = gridSpec.originWgs84;

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
  const parts = text.split(',').map((value) => Number(value.trim()));
  if (
    parts.length !== 3 ||
    parts.some((value) => !Number.isInteger(value)) ||
    parts.some((value, index) => value < 0 || value >= gridSpec.shape[index])
  ) {
    throw new Error('좌표는 격자 범위 안의 정수 x,y,z 형식이어야 합니다.');
  }
  return parts;
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
