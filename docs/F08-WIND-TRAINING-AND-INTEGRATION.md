# F08: NOAA observation → ERA5 Random Forest correction → voxel risk → A*

## Implemented
- Historical ERA5 endpoint automatically loads `models/wind_corrector.pkl` **only if a real trained model exists**. Response includes `ai_corrected`, original/corrected speed, station, and metrics. Without a model, it returns uncorrected ERA5 and `ai_corrected: false`.
- The web UI displays the correction provenance and applies the returned wind to the risk map. Regenerate candidate routes after changing weather.
- Voxel risk scales with wind speed using 8 m/s as the previous map's reference. This is a **heuristic**, not calibrated wind CFD or spatial wind measurements.

## Obtain observations
1. Choose a weather station near Songdo using NOAA/NCEI station metadata. Record the exact station ID, station coordinates, and date coverage. Incheon Airport is a plausible *candidate*, not a confirmed representative Songdo urban-canyon station.
2. Download **Global Hourly / ISD CSV** containing `DATE` and `WND` columns for a common historical year (e.g. 2024) from NOAA's ISD CSV archive (now hosted via NOAA Open Data Dissemination). NOAA's newer GHCNh PSV/Parquet is **not** directly supported by this CSV parser.
3. Save it locally as `backend/data/noaa_station.csv`. Do not commit large station datasets or private credentials. Confirm `DATE` timestamps are UTC, `WND` speed is tenths of m/s, and observation coverage overlaps ERA5.

## Train locally (from backend directory)
```bash
python -m pip install -e '.[ai,dev]'
python -m app.ai.wind_correction \
  --noaa-csv data/noaa_station.csv \
  --station YOUR_NOAA_STATION_ID \
  --latitude YOUR_STATION_LATITUDE \
  --longitude YOUR_STATION_LONGITUDE \
  --start 2024-01-01 --end 2024-12-31
```
The command needs >=100 paired hourly observations and writes `models/wind_corrector.pkl` plus `models/wind_corrector.metrics.json`. Compare `corrected_mae_mps` with `baseline_mae_mps`: do not claim improvement unless corrected MAE is lower on the held-out time period. Check station distance and representativeness.

## Run
```bash
python -m uvicorn app.main:app --reload --port 8000
```
Open the UI, select **ERA5** historical weather and a date within the training period, then apply. If model exists, the API predicts a corrected station-level speed and the UI updates voxel risk. Click **Generate candidate routes** again to use the corrected risk.

## Limits
- The trained model is a station-level statistical correction of ERA5 10 m wind, not a learned 3D urban wind field. Wind direction is not corrected.
- NOAA observed mode displays the station's actual observation directly; no AI correction is applied to an observation.
- Existing simulation does not guarantee routes change for every wind scenario. Flight safety cannot be inferred from this prototype.
- Loading the local pickle file assumes it was created by a trusted local training process.
