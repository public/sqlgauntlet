# Contributing

- **Setup:** `uvx pre-commit install` once. The hooks run ruff (lint and format), clang-format (`.clang-format`),
  shellcheck, and two local checks:
  - no file of a "fetch"-class corpus app is committed (`tools/check-corpus.py`);
  - no home-directory or temp paths.
- **Tests:** `make test` builds the stock and libSQL backends and runs `tests/smoke.py` over a few vendored apps.
  `make lint` runs every hook over the tree. CI runs both on Linux (x86-64, arm64; gcc and clang) and macOS.
- **A new backend:** a shared object exporting `sqlgauntlet_backend_v1()` (`include/sqlgauntlet.h`), plus a preset
  in `python/sqlgauntlet/run.py` if it should have a short name. Leave optional hooks NULL rather than faking them.
- **The corpus:** change it upstream in cursed-sqlite-turbo's `bench/corpus`, then re-pin with
  `tools/corpus-split.py`. A new app's licence must be classed in that script (vendor or fetch) before it can come in.
- **Measurements:** decide on Linux instruction counts and same-run interleaved arms (README, "Performance"). macOS
  wall times are a data point, not a verdict.
