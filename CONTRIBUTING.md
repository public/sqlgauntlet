# Contributing

- **Setup:** `uvx pre-commit install` once. The hooks run ruff (lint and format), clang-format (`.clang-format`),
  shellcheck, and two local checks:
  - no file of a "fetch"-class corpus app is committed (`tools/check-corpus.py`);
  - no home-directory or temp paths.
- **Tests:** `make test` builds the sqlite and libsql backends and runs `tests/smoke.py` over a few vendored apps and
  `tests/workloads.py` over TPC-H's path. `make test-postgres PG_URL=…` runs `tests/postgres.py`; it needs a server,
  for which `backends/postgres/server.sh` starts one in Docker, and sqlglot.
  `make lint` runs every hook over the tree. CI runs both on Linux (x86-64, arm64; gcc and clang) and macOS.
- **A new backend:** `backends/<name>/` with its adapter (a table of `include/sqlgauntlet.h`'s functions, exported
  as `sqlgauntlet_backend_v1()`), a `fetch.sh` that pins its sources by commit or checksum, and a `backend.mk` that
  builds `build/backends/<name>.so` and its `.json` (`$(call backend_json,...)`). Leave optional hooks NULL rather
  than faking them. A SQLite-API library needs only `make sqlite-api`.
- **The corpus:** it lives here: change `corpus/projects/<app>/`, and keep each app's licence and NOTICE.md beside its SQL. A new app's licence must be classed in that script (vendor or fetch) before it can come in.
- **Measurements:** decide on Linux instruction counts and same-run interleaved arms (README, "Performance"). macOS
  wall times are a data point, not a verdict.
