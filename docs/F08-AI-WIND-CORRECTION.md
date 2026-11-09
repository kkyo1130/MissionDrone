# F08 — Public observations wind correction (prototype)

This module compares **NOAA Global Hourly station wind observations** (DATE and WND fields)
with **Open-Meteo ERA5 reanalysis** at the station coordinates. It trains a Random Forest
to predict station-observed wind speed from ERA5 speed/direction and hour. The test
set is the last ~20% of time, separated by a day boundary. Baseline and corrected
MAE/RMSE are reported. No fabricated observations are used.

## Run

From the repository root:

```bash
cd backend
python -m pip install -e '.[ai,dev]'
python -m app.ai.wind_correction \
  --noaa-csv /path/to/NOAA-global-hourly-station.csv \
  --station YOUR_STATION_ID \
  --latitude STATION_LATITUDE --longitude STATION_LONGITUDE \
  --start 2024-01-01 --end 2024-12-31
pytest -q
uvicorn app.main:app --reload
```

The NOAA CSV must have `DATE` and `WND` columns, with WND in NOAA's
comma-separated direction/quality/type/speed/quality format. A public CSV URL
may be used instead of a local file. Download the station file from the
[NOAA Global Hourly open dataset](https://registry.opendata.aws/noaa-isd/)
and verify its station coordinates and reporting period. The training script
fetches ERA5 via Open-Meteo's public historical API. Network access is required.

After training:
- `GET /api/ai/status`
- `POST /api/ai/correct-wind` with
  `{"wind_speed_mps":8,"wind_direction_deg":90,"timestamp_utc":"2024-07-01T12:00:00Z"}`

Models are written to `models/wind_corrector.pkl` at the repository root,
and metrics to `models/wind_corrector.metrics.json`. Do not commit model
artifacts or load pickles from untrusted sources.

**Limitations:** Station-level observation correction does **not** measure
building-induced turbulence or per-voxel urban-canyon risk. This model is
deliberately not wired into A* safety costs or autonomous flight. Integrating
it into voxel risk correction requires co-located urban wind measurements,
spatial features, out-of-sample validation, and a conservative fallback.
ERA5 is reanalysis, not a direct measurement.

## Applying historical weather to the 3D environment

1. Download an actual NOAA Global Hourly station CSV containing `DATE` and `WND`.
2. Set `NOAA_STATION_CSV` to its **absolute path**, and `NOAA_STATION_ID` to the station identifier.
3. Run the backend and open the Cesium dashboard.
4. Enter a **UTC** historical timestamp (the date/time control is interpreted as UTC).
5. Choose **NOAA 관측소 실측**, then click **과거 기상 데이터 적용**.
6. The selected station wind speed/direction update the simulation input fields and 3D risk map.
   Generate new candidate routes to use the updated wind. Repeated simulation uses the
   selected historical wind scenario. Existing routes are not retroactively recomputed.

Alternatively choose **ERA5 재분석**: this fetches historical reanalysis for the
configured `WEATHER_LATITUDE` and `WEATHER_LONGITUDE` (defaults 37.37,126.62).
ERA5 is **not** direct observation and is labeled separately. NOAA is never silently
replaced by ERA5 when the station file is missing.

The risk map still uses a simplified heuristic; this feature is *historical
weather input integration*, **not** learned per-voxel wind correction. The AI
station wind model remains opt-in via `/api/ai/correct-wind` and is not
automatically applied to flight safety decisions.
