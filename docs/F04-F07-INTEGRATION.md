# F04–F07 backend integration status

Source: PR #2 (`feat/F03-backend-p0`). Integration target: canonical `main` with F00–F03 already merged (#3, #4, #8).

## Compatibility decisions

1. Preserve `app/main.py`, `app/schemas/missions.py`, and the `mission_type` / voxel point / nested constraints API contract.
2. Preserve the F03 candidate-route generator and transform `coordinates[].enu_m` into meter-based simulation inputs.
3. F04: simulate synthetic winds with deterministic seeds; results are **not flight-safety validated**.
4. F05: rank routes only when every selected scenario/repeat passes constraints; return no recommendation if none qualify.
5. F07: SQLAlchemy-backed mission, route, and experiment records, with replay and JSON/CSV export. Tables currently use `Base.metadata.create_all()`; a versioned schema migration strategy (e.g. Alembic) remains future work.
6. F08: preserve F00–F03 regression coverage and add SQLite integration tests.

## Verified checkpoints

- [x] F04 synthetic wind simulation integrated
- [x] F05 safety-first route comparison/recommendation integrated
- [x] F07 MySQL/SQLite persistence, deterministic replay, JSON/CSV export implemented
- [x] GitHub Actions pull-request regression/integration job passed on commit `4b94750fa03393c561a9699db8f3b59fc8635454` (SQLite)
- [x] User's local backend pytest: 20 passed (reported)
- [x] User's local MySQL 8.4 Docker connection and table initialization succeeded (reported)
- [x] User's local MySQL E2E created a mission, three routes, six simulation runs; records were subsequently readable (reported)
- [x] User's replay endpoint returned `matches=true` and CSV export returned 200 (reported)
- [ ] One strict API response-equality assertion in the user's custom MySQL E2E failed; exact differing field remains undiagnosed. The later database existence/replay checks do not resolve this assertion.
- [ ] Automated MySQL-backed integration tests in CI
- [ ] Versioned schema migrations
- [ ] Cesium frontend/API smoke test verified on merged `main`

## Notes

This integration preserves the existing F00–F03 behavior instead of directly merging overlapping PR #2. Current weather, energy, and risk calculations use synthetic models and do not guarantee real-world flight safety.
