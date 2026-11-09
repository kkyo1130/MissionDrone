const statusEl = document.getElementById('status');
const startInput = document.getElementById('start');
const endInput = document.getElementById('end');
const windSpeedInput = document.getElementById('windSpeed');
const windDirectionInput = document.getElementById('windDirection');
const gridSpec = window.APP_GRID_SPEC;

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
  destination: Cesium.Cartesian3.fromDegrees(126.62, 37.37, 3000),
  duration: 0,
});

const origin = Cesium.Cartesian3.fromDegrees(126.62, 37.37, 0);
const enuToFixed = Cesium.Transforms.eastNorthUpToFixedFrame(origin);
let routeEntity = null;
let riskEntities = [];

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

async function loadRiskMap() {
  const params = new URLSearchParams({
    wind_speed: windSpeedInput.value,
    wind_direction: windDirectionInput.value,
    threshold: '0.7',
  });
  const response = await fetch(`/api/risk-map?${params}`);
  const data = await response.json();
  if (!response.ok) {
    throw new Error(data.detail || 'Risk map API 요청 실패');
  }

  clearRisk();
  for (const point of data.points) {
    riskEntities.push(viewer.entities.add({
      position: voxelToCartesian([point.x, point.y, point.z]),
      box: {
        dimensions: new Cesium.Cartesian3(
          data.voxel_size_m,
          data.voxel_size_m,
          data.voxel_size_m,
        ),
        material: Cesium.Color.fromCssColorString('#d06f2f').withAlpha(
          Math.min(0.95, point.risk),
        ),
        outline: false,
      },
    }));
  }

  statusEl.textContent = `Risk voxels: ${data.points.length}, wind=${data.wind_speed}, dir=${data.wind_direction}`;
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

document.getElementById('btnRoute').addEventListener('click', async () => {
  try {
    await findRoute();
  } catch (error) {
    statusEl.textContent = error.message;
  }
});

loadRiskMap().catch((error) => {
  statusEl.textContent = error.message;
});
