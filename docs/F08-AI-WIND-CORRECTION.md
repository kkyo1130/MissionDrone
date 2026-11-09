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
