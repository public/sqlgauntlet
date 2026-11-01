# SQLite-family backends on the full corpus, 2026-11-01

- **Corpus:** 1,302 statements (upstream 0c63afa, without the 5 `heavy` ones).
- **Machine:** Apple M-series, macOS. It was shared with other work, at load average 12–19.
- **Backends:**
  - `sqlite`: SQLite 3.54.1;
  - `turbo`: cursed-sqlite-turbo at 9a37463 (`contrib/turbo`, `QC_SERVE=1`);
  - `libsql`: libSQL 0.2.3, which is SQLite 3.47.0.
- **Build:** every backend was built with the same options (`backends/sqlite/backend.mk`).

## Correctness (`sqlgauntlet-run check`, 8 runs per statement)

| arm | same as sqlite | rows differ | error | manifest row counts |
|---|---:|---:|---:|---|
| turbo | 1,302 | 0 | 0 | 1,302 agree |
| libsql | 1,296 | 2 | 4 | 1,302 agree (of those that ran) |

sqlite itself: 1,302 ok, 0 unstable, all 1,302 row counts equal to the manifest's.

**Where libSQL disagrees** (in every case, sqlite and turbo agree with each other):
- **4 errors: `no such function: percentile`.** These are the mlflow `trace_metrics.latency_percentiles_daily` and
  `trace_metrics.span_latency_p95_by_name`, and the phoenix `metrics.latency_percentiles_hourly` and
  `project.latency_p50` statements. `percentile()` is a compile-time option in SQLite ≥ 3.51, and libSQL's 3.47 base
  predates it.
- **2 different results, both from one `unixepoch()` change:**
  - **The statements:** phoenix `experiments.avg_latency` (2 of 20 groups' averages off by 1 ms) and
    `spans.filter_llm_latency` (a latency filter that admits 2 rows and drops 2 others).
  - **The cause:** both compute latency as `unixepoch(end) - unixepoch(start)` on timestamps with microseconds.
    SQLite 3.47 rounds the fractional second to milliseconds before truncating, so `…05.9995` and later count as
    second 6. SQLite 3.54 truncates, so they count as second 5. Checked directly:
    `unixepoch('2026-10-29 23:15:05.9995')` is 1793315705 in 3.54.1 and 1793315706 in 3.47.0.
  - **Who's affected:** every app running this phoenix query on a pre-3.48 engine gets latencies that are 1 s off
    near second boundaries.

**turbo against its own harness:** `tools/identity.py` compared sqlgauntlet's sqlite and turbo arms with
cursed-sqlite-turbo's own bench driver (`bench/corpus/corpus.c`), built over the same two engines. All 1,302 C lines
were identical in both pairs: status, rows, changes, ordered and multiset hashes, and errors. The 50 statements that
read the clock were held to their row counts. Two arms differed only in turbo's alt-path counts (turbo's adapt step
decides on timings), on 1 statement.

## Timings

There was no corpus-wide speed run. The machine was loaded, so local wall times would have been noise. Decisions need
Linux instruction counts with same-run interleaved arms (README, "Performance"). The per-arm check times above are
only for scale: about 3.5–4.5 minutes per arm for 1,302 statements × 8 runs.

## Not done

- **Turso:** its adapter is written, but its Rust build (`backends/turso/fetch.sh build`) hasn't been run.
- **DuckDB, PostgreSQL, MySQL:** the dialect layer exists, but there are no adapters.
