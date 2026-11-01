# Provenance

The harness and its workloads started as the benchmark and test apparatus of
[cursed-sqlite-turbo](https://github.com/public/cursed-sqlite-turbo), on its branch `claude/bench-110a4d` at 4affcbd.
They were imported on 2026-11-01 without their history, which stays in that repository, and rewritten here to work
against any backend:

| here | there |
|---|---|
| `src/driver.c` | `bench/corpus/corpus.c`: the same modes and output lines, over the backend table; callgrind from its `-DCORPUS_CALLGRIND` |
| `src/driver.c`'s `load` | `bench/tpch/load.c`, `bench/clickbench/load.c` |
| `python/sqlgauntlet/` | `bench/corpus/{prep,compare,summarise}.py`, `cg.py`, `run.sh`'s arms and rounds |
| `workloads/tpch/` | `bench/tpch/` (schema, indexes, the 22 queries, `fetch-dbgen.sh`) and `bench/small/queries.sql` |
| `workloads/clickbench/` | `bench/clickbench/` (`fetch.sh`, `cut.py`) |
| `workloads/speedtest1/` | `bench/speedtest1/` (`summarise.py`, `instrument.py`, `st1_instr.h`), `scripts/fetch-sqlite-src.sh` |
| `workloads/sqlancer/` | `tests/sqlancer/` |
| `workloads/classes.tsv` | `bench/classes.tsv` |
| `corpus/` | `bench/corpus/` at 0c63afa (corpus/UPSTREAM), split by licence; `corpus/tools/` and its INDEX, STATS, SURVEY and scores files unchanged |

The rest stayed with turbo:
- the engine's own builds: `bench/build-sqlite.sh`'s serve and diff variants, and `bench/qc_serve.c`;
- its tuning arms;
- its diagnosis modes (explain under QPSG, perf record and annotate);
- `qrun.c` and `parity.py`, which the driver and summarise now cover;
- the corpus explorer;
- the payloads for its benchmarking boxes.
