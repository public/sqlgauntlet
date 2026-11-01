# What cursed-sqlite-turbo's bench had, and what sqlgauntlet took

The inventory was taken on 2026-11-01:
- turbo `main` at 5a450c4;
- the bench branch `claude/bench-110a4d` at bdf0c9c;
- the release-prep branch at 0cfdf6a.

Each piece below says what it is in turbo, and whether sqlgauntlet took it, generalised it or left it in turbo.

## Generic, now in sqlgauntlet

| turbo | what | sqlgauntlet |
|---|---|---|
| `bench/corpus/` (projects, manifest.jsonl, tools/run.py's parser) | 1,301 statements (1,306 with the 5 `heavy` ones) from 44 apps, each with schema, deterministic data, parameters and the expected rows; release-prep: 1,276 from 43 apps, with per-app LICENSE/NOTICE | `corpus/`: 19 permissive apps vendored, 24 fetched at a pinned turbo commit (`corpus/fetch.sh`); any directory in that layout works with `--corpus` |
| `bench/corpus/prep.py` | corpus → flat `.q` and `.build.sql` per app | `python/sqlgauntlet/prep.py`, same output for SQLite; reads split manifests; dialect hook |
| `bench/corpus/corpus.c` | build / check / hot / prep / explain over SQLite's API; FNV row hashes in order and as multisets; served flag; perf counters; per-execution E lines; BEGIN…ROLLBACK for writes; literal parameters evaluated by the engine | `src/driver.c` over `sg_backend`: the same modes and C/T lines, byte for byte; added normalised hashes (N lines), `rows` mode, `engine-ms` in E lines |
| `bench/corpus/regexp.c` | SQLite's ext/misc/regexp.c, as the CLI the corpus was recorded with | `adapters/regexp.c`, registered by the SQLite adapter |
| `bench/corpus/run.sh` (check, speed, ROTATE) | arms, one DB copy per arm, rounds, rotation | `python/sqlgauntlet/run.py`: arms as backend + environment; rotation by round *and* app; `env.json` |
| `bench/corpus/compare.py` | each arm against stock: status, rows, hash (ordered or multiset), wallclock by count | `compare.py`: N-way, verdict per arm (types / order / rows / rowcount / error / ...), groups of agreeing arms |
| `bench/corpus/summarise.py`, `bench/parity.py` | geomeans with bootstrap intervals over rounds; by tag, by app; losers | `summarise.py`: the same, plus distributions, size buckets, warm-up vs steady state from E lines |
| `bench/lib.sh` compile options | one set of options for every arm (only the engine differs) | `Makefile` `SQLOPTS`, the same list |
| `scripts/fetch-sqlite.sh` | SQLite 3.54.1, SHA-256 pinned | `scripts/fetch-sqlite.sh`, plus `fetch-libsql.sh` and `fetch-turso.sh` |
| row-count checks against the manifest | caught 4 driver bugs (anonymous `?` bound as NULL, only 64 parameters bound, regexp differing from stock, …) | the corpus's `rows` stays in the manifest; `check` reports row counts per arm, and `identity.py` holds the driver to turbo's |

## Generic, not yet in sqlgauntlet (next)

| turbo | what | why not yet |
|---|---|---|
| `bench/speedtest1/`, `bench/tpch/` (pinned dbgen, 22 queries), `bench/clickbench/` (checksummed 1M-row prefix) | standard workloads with row hashes per arm | each is a workload shaped as a corpus app would fit them; TPC-H and ClickBench need dialect overrides for DuckDB/PostgreSQL, which is where they pay off |
| `tests/sqlancer/` | SQLancer campaigns per variant, through sqlite-jdbc's JNI library rebuilt from each | JVM and JNI; a backend-generic version would talk to sqlgauntlet backends through a JDBC shim: its own project |
| `bench/corpus/cg.py`, run.sh `cg` | callgrind instruction counts (deterministic, any Linux) | a driver build flag (`-DCORPUS_CALLGRIND` toggles collection around the timed spans); easy to port when wanted |
| `bench/corpus/explorer/` | a web page over the corpus and a run | reads turbo's files; would read sqlgauntlet's run directories unchanged once pointed at them |
| `bench/xeon/payload.sh`, `box-run.sh` | broker payloads: one arm per run, ccache, clang, env.txt | the broker contract is cursed-http's; sqlgauntlet's equivalent is a payload that ships `build/`'s sources, the corpus and `run.py` (the box records env.json itself) |

## Turbo-specific: stays in turbo

| turbo | what |
|---|---|
| `bench/build-sqlite.sh` variants `serve`, `diff` and `native` (qc/patch.py, the step hook, qc_serve.c) | how to build turbo; sqlgauntlet's Makefile builds only `native` (QC_SERVE=1), the shipped one |
| arms `servevm`, `noreoffer`, `margin098`, `alignoff`, … (QC_* environment) | turbo's tuning knobs: in sqlgauntlet they're just `--arm name=so:build/backends/turbo.so:QC_ADAPT_MARGIN=0.98` |
| `corpus.c explain` (EXPLAIN under QPSG), `diag`, `prof` (perf record + annotate on `qv_region`), CORPUS_EXTRA's X and S lines (cache, TLB, front-end counters, allocation counts) | turbo diagnosis; sqlgauntlet keeps an optional `explain` hook in the ABI for the generic part |
| `tests/tcl_qc.c`, `tests/slt_qc.c`, SQLite's TCL suite and sqllogictest runs | conformance of turbo against SQLite's own tests, through turbo's hooks; sqllogictest itself is generic (it's a cross-engine format), and the natural second corpus for sqlgauntlet, but its runner here is turbo's |
| `L` lines (lookaside use) | SQLite internals |

## What "served" means outside turbo

turbo serves a statement when its compiled plan runs instead of SQLite's VM. That is turbo's adapt verdict, and it
can change during the first executions. sqlgauntlet keeps it as an optional per-execution flag: "the backend's fast
path ran". The warm-up/steady-state split is taken from it. A backend without such a flag reports -1, and its warm-up
is the first execution alone.
