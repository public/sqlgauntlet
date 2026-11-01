# contrib/turbo

[cursed-sqlite-turbo](https://github.com/public/cursed-sqlite-turbo), a specialising layer over SQLite, and the
benchmark and test apparatus that grew up around it. These came from turbo's repository; PROVENANCE.md says from
where. The engine itself stays in turbo. This directory builds it from a pinned commit (`TURBO`: url, commit).

```bash
sh contrib/turbo/build.sh [turbo-clone]
```

```bash
cd python && python3 -m sqlgauntlet.run check --arm sqlite --arm turbo --arm turbo-serve
```

- **The build** goes through the generic `make sqlite-api` and produces three backends from turbo's
  `bench/build-sqlite.sh` variants:
  - `turbo` is the shipped native build. Its arm defaults set `QC_SERVE=1`.
  - `turbo-serve` puts qc behind SQLite's API for unmodified programs (`bench/qc_serve.c`).
  - `turbo-diff` checks every statement value by value against the interpreter.
- **turbo's own bench driver** is built too, as `build/ref/corpus-*`. These are exe arms that `tools/identity.py`
  holds sqlgauntlet to. On the 1,302-statement corpus, every line was identical.
- **The apparatus:** `sh contrib/turbo/tree.sh` assembles `build/turbo-tree`, which is turbo at the pin with these
  files back where they lived. Its scripts run there unchanged:
  - `bench/corpus/run.sh` (check, speed, cg, prof, diag; arms such as `margin098`);
  - `bench/speedtest1/`, `bench/tpch/` (pinned dbgen) and `bench/clickbench/`;
  - `bench/qrun.c`, `compare.py` and `parity.py`;
  - `bench/xeon/` (payloads for cursed-http's Xeon broker);
  - `tests/sqlancer/` (campaigns, replay, repro, reduce).

  `BENCH.md` documents all of it, with its results.
- **What turbo can now delete:** REMOVE-FROM-TURBO.md.
- **What's still to do:** the workloads (speedtest1, TPC-H, ClickBench, SQLancer) are engine-neutral in what they
  run, but their scripts build turbo's variants themselves. The next step is to port each one to the backend ABI as
  a corpus-shaped workload, so it runs against any backend. Until then, they live here.
