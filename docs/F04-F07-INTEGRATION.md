# F04–F07 backend integration plan

Source: PR #2 (`feat/F03-backend-p0`). Target: current `main` (F00–F03 integrated via #3, #4, #8).

## Compatibility decisions

1. Keep `backend/app/main.py` and `backend/app/schemas/missions.py` as canonical API. Existing mission requests use `mission_type`, `start`/`end` voxel indices and nested `constraints`; PR #2 uses `kind`, `start`/`goal` local-meter Point objects and flattened constraints. Do not overwrite the canonical endpoints.
2. Keep `backend/app/core/routing.py` as the route generator; its output includes `points` (voxel indices), `coordinates[].enu_m`, `distance_m`, `estimated_time_s`, `estimated_energy_wh`, and `risk_exposure`. Convert to meter-based ENU only at the simulation boundary.
3. F04: extract PR #2's wind simulation (crosswind, groundspeed, climb rate, energy, risk and budget checks) into a separate module and adapt it to the canonical mission and route models. Retain explicit synthetic-data caveat and deterministic seeds.
4. F05: extract PR #2's safety-first evaluation and ranking; reject candidates failing any selected weather run. Use the existing mission weights or document any deliberate override. Return no recommendation if all candidates fail.
5. F07: port `backend/app/store.py` with SQLAlchemy models and transaction boundaries. Preserve current endpoint contracts; add database-backed persistence via an explicit migration strategy, not by replacing the existing FastAPI app factory. Use SQLite for unit tests and MySQL for integration tests.
6. F08: add regression tests covering F00–F03 APIs, multiple wind scenarios, repeatability, no-safe-route behavior, persistent storage, replay, and JSON/CSV exports.

## Integration checkpoints

- [ ] F04 simulation adapter implemented and tested
- [ ] F05 evaluation/recommendation API implemented and tested
- [ ] F07 persistence, schema migration, replay/export implemented and tested
- [ ] F00–F03 regression tests pass
- [ ] SQLite and MySQL integration tests pass
- [ ] Cesium frontend/API smoke test passes
- [ ] Merge integration PR to main; only then close superseded PR #2

PR #2's reported tests were run on its own independent backend, not on the combined main codebase. This branch starts with integration decisions; it does **not** claim F04–F07 are implemented yet.
