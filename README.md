# sqlgauntlet

A correctness and performance harness for SQL engines. It runs a corpus of real application statements through
several backends and reports two things. First, **where the backends disagree**: which statements, how, and which
backends side with which, not just a pass rate. Second, **how fast each backend is** against a reference, with
warm-up measured separately from steady state.

| backend | what | status |
|---|---|---|
| `sqlite` | SQLite 3.54.1 | built by `make backends` |
| `libsql` | libSQL 0.2.3 (a SQLite 3.47.0 fork), its bundled amalgamation | built by `make backends` |
| `turso` | Turso 0.8.3 (a Rust rewrite of SQLite's engine), over its C ABI (`sdk-kit`) | `sh backends/turso/fetch.sh build` (Rust), then `make turso` |
| `postgres` | PostgreSQL, a client over libpq; statements translated by sqlglot, data exported from the SQLite build | `make postgres PG_URL=…` (libpq, `pip install sqlglot`); `backends/postgres/server.sh` starts a throwaway server in Docker |
| `js-node`, `js-bun`, `js-sqljs`, `js-wasm` | SQLite through JavaScript bindings: `node:sqlite` (Node.js 26), `bun:sqlite` (Bun 1.4), sql.js 1.15 and the official `@sqlite.org/sqlite-wasm` 3.54.1; exe arms over `backends/js/driver.mjs`, which reads and binds values through each binding's own API. They find binding and value-conversion bugs, and each binding's build options, not engine bugs | `make js` (fetches pinned runtimes and packages), then `--arm js-node=exe:build/backends/js-node` |
| any SQLite-API library | a fork or a patched build: `make sqlite-api NAME=<n> SRC=<dir>` | built from your tree |
| DuckDB, MySQL | other dialects, through the same dialect layer | not yet |

## Quick start

```bash
make backends
```

```bash
sh corpus/fetch.sh <clone of the corpus upstream>
```

```bash
cd python && python3 -m sqlgauntlet.run check --arm sqlite --arm libsql
```

```bash
cd python && python3 -m sqlgauntlet.run speed --arm sqlite --arm libsql --rounds 3 --exec-lines
```

`fetch.sh` gets the corpus apps that can't be committed here (see [corpus/README.md](corpus/README.md)). Without it,
runs use the 1,245 committed statements. Results go to `build/run/` (or `--out`): one TSV per arm and mode, plus
`env.json`. The Markdown report is printed on stdout.

## The corpus at a glance

The corpus is the point of sqlgauntlet: statements real applications send to SQLite, copied from their source at a
pinned commit, not benchmark queries written for an engine. The numbers below are for the 1,245 statements committed
here, from 42 applications; `corpus/fetch.sh` adds 62 more from two apps whose licences keep them out of the
repository (1,307 from 44 in all).

| | |
|---|---:|
| statements | 1,245 (1,070 reads, 175 writes) |
| applications | 42, 20 to 35 statements each, every one with its repository, commit and `file:line` |
| SQL | 722 KB, 8,290 lines; median statement 311 bytes, longest 9.9 KB |
| bytecode | 262,199 VDBE ops in all; median 77 per statement, largest 17,379 |
| rows returned | 1.94 million at scale 1; the largest result 140,788 rows |
| stock SQLite time | 82 s for one pass (indicative); median 0.9 ms, 289 statements under 0.1 ms, 56 at 100 ms or more |
| licences | MIT 297, AGPL 338, GPL 267, Apache 182, MPL 66, EUPL 60, BSD 35: each kept with its own app |

They are not toy queries:
- 41% join three or more tables, 38% have a subquery (14% correlated), 44% need a temporary B-tree for sorting or
  grouping, and 40% scan a whole table;
- 59% are built at runtime by the app (ORMs, query builders, string concatenation), 66% are on a hot path such as a
  page load, a keystroke or a sync loop, and 18% are microsecond point lookups where per-call overhead is everything;
- there are window functions, recursive CTEs, FTS4/FTS5 `MATCH`, JSON functions, `COLLATE NOCASE`, upserts,
  `RETURNING`, virtual tables and the odd `REGEXP` (corpus/STATS.md has every tag).

Some favourites:

| statement | what it is | why it's interesting |
|---|---|---|
| `grocy.mealplan.recipes_resolved_week` | grocy's meal-plan page: recipe fulfilment over a 13-day window | 17,379 VDBE ops and 552 table accesses through nested views; 10-15 s on stock SQLite, as grocy users report (grocy#2992). One of five tagged `heavy`. |
| `zotero.items.primary_data` | loading Zotero's item tree | 9.9 KB of SQL: a 10-way LEFT JOIN with dozens of correlated subqueries per row building each item's first creator; 24,356 rows |
| `firefox.autofill.origin_history_bookmark` | URL-bar autofill, run on every keystroke | four window aggregates over a CTE, a correlated subquery, then a hash probe, in 3.5 ms |
| `joplin.search.fts_notebook_tag_text` | Joplin's filtered full-text search | a recursive notebook walk and an `INTERSECT` of tags feeding FTS4 `MATCH` with `offsets()` and `matchinfo()` |
| `technitium.querylogs.page.last_page` | jumping to the last page of a DNS query log | `OFFSET` skips about 250,000 rows to return 25 (Technitium DnsServer#563) |
| `chromium.history.batch_recent_visits_significant` | rebuilding the omnibox index | 65 VDBE ops that return 140,788 rows: almost all the time is moving rows |
| `firefox.utils.bookmarks_tree` | bookmark backups and sync | a recursive descendants CTE with a per-row icon subquery, ordered by depth |

## Design

```
 corpus/  projects/<app>/{schema,data,queries}.sql, manifest*.jsonl
    │  python/sqlgauntlet/prep.py      (dialect.py for other dialects)
    ▼
 <app>.build.sql, <app>.q, <app>.expected.tsv
    │  python/sqlgauntlet/run.py       arms, interleaving, env.json
    ▼
 sqlgauntlet --backend <b>.so <mode>   src/driver.c over include/sqlgauntlet.h
    │                                  (or any program with the same command line: an "exe" arm)
    ▼
 <arm>.check.tsv, <arm>.<hot|prep>.<round>.tsv     check / norm / timing / exec lines
    │  compare.py, summarise.py
    ▼
 Markdown: disagreements, timings
```

### The backend interface (`include/sqlgauntlet.h`)

A backend is one table of C functions, `sg_backend`: `open`, `close`, `exec` (scripts), `prepare`, `param_count`,
`param_index`, `bind`, `step`, `column_count`, `column`, `reset`, `finalize`, `changes`, `errmsg`. The optional hooks
are `exec_begin`, `exec_stats` (facts the driver can't see), `counter_names` and `explain`.

- **Values:** `sg_value` carries one of five kinds: integer, float, text, blob, NULL. An engine with richer types maps
  each to one of them, and its adapter says how.
- **Backends are shared objects** exporting `sqlgauntlet_backend_v1()`, in `build/backends/<name>.so`. Beside each
  are `<name>.json` (its default environment) and `<name>.so.cflags` (how it was built). The CLI loads one with
  `--backend` (dlopen, `RTLD_LOCAL`), so two builds of one engine never share symbols.
- **In-process engines** fill the table over their own API: `backends/sqlite/adapter.c` serves any library with
  SQLite's C API, and `backends/turso/adapter.c` serves Turso's.
- **Networked engines (client adapters)** fill the same table with a client: `open` connects, `prepare` is a
  server-side prepare, `step` fetches the next row. Their `exec_stats` returns `engine_ms`, the server's own measure
  of the execution: PostgreSQL's `pg_stat_statements` delta or `EXPLAIN (ANALYZE)`, MySQL's `performance_schema`
  statement timers. The driver times the wall span as for any backend and prints `engine_ms` on each exec line, so
  `wall − engine` is the protocol and client cost. No client adapter is written yet.
- **The alternative path** (`alt_path`): an engine with more than one way to run a statement reports which one it
  took. Examples are a compiled or specialised plan, a JIT, or a result cache. The driver counts these executions
  (`alt/runs`), and the warm-up/steady-state split is taken from them. The SQLite-API adapter reports a run that never
  stepped SQLite's bytecode VM. A backend without such a notion reports -1.

### The driver (`src/driver.c`, `libsqlgauntlet.a`)

| mode | what | lines |
|---|---|---|
| `build` | the app's database from its build script | |
| `load` | a data file (`tbl`, `csv`, or `csvn`, where an unquoted `\N` is NULL) into a table, through the backend's prepare and bind, every field as text | |
| `check` | each statement 8 times: 6 on one handle, then 2 freshly prepared; the runs must agree with each other | `check stmt status rows changes hash multiset_hash alt/runs error`, `norm stmt norm_hash norm_multiset_hash` |
| `hot` | prepared once, then 1 + reps executions (reset between), until `budget-ms` is spent | `timing stmt rows hash first_ms median_ms mean_ms runs instructions cycles alt/runs error` |
| `prep` | every execution prepares, binds, steps and finalizes | the same timing line |
| `rows` | one statement's normalised rows (for reading a disagreement) | `rows` |
| `info` | the backend's name, dialect and version | `info backend dialect version` |

- **Writes:** each write runs inside `BEGIN … ROLLBACK`, outside the timed span. Reads run in autocommit.
- **Parameters:** each backend evaluates the corpus's literal parameters itself and binds them by name, or by position
  for anonymous `?`.
- **Counters:** on Linux, user-mode instructions and cycles come from `perf_event_open` over the timed spans
  (`perf_event_paranoid ≤ 2`); elsewhere they are -1.
- **Exec lines:** `SG_EXEC=1` prints one per execution: `exec stmt run alt wall_ms instructions cycles engine_ms`.
- **Where the time goes:** `SG_EXTRA=1` and `SG_SPLIT=1` (`sqlgauntlet-run speed --extra-counters`, `--split`) add
  seven columns to each exec line, -1 where off or unavailable: `kernel_cycles branch_misses page_faults
  context_switches step_ms column_ms reset_ms`. Kernel-mode cycles need `perf_event_paranoid ≤ 1` (or root); the
  split times the backend's step calls, its column reads and the reset after the execution (outside `wall_ms`).
  summarise then reports each side's kernel share, faults, switches and split, and the statements whose kernel share
  differs most: wall time the user-mode counters can't see. `--min-reps N` gives every statement N executions whatever
  the budget, so an adaptive engine reaches its steady state.
- **Readable files:** sqlgauntlet-run starts every .tsv with a `#` line per record type naming its columns, e.g.
  `# exec stmt run alt wall_ms instructions cycles engine_ms`. The record tags were single letters before
  (`C N T E R I`); `python/sqlgauntlet/lines.py` reads both, so older results still parse.
- **Callgrind:** `make callgrind` builds the CLI with `-DSG_CALLGRIND`. `sqlgauntlet-run speed --callgrind` then counts
  instructions under valgrind, collecting only around the timed spans, and dumping once per statement and mode. The
  counts are deterministic on any Linux, so they can measure statements too short to time.

### Correctness: normalisation and verdicts (`python/sqlgauntlet/compare.py`)

Every result is hashed twice:
- **Strict** (`hash`, `multiset_hash`): each value's kind and bytes. This is for engines that must match each other exactly,
  such as a fork or a patched build against its base.
- **Normalised** (`norm_hash`, `norm_multiset_hash`):
  - **Numbers:** integers and floats are one kind. A float with an exact integer value within 2^53 counts as that
    integer; other floats are rounded to `SG_DIGITS` significant digits (default 12).
  - **Text and blobs** keep their bytes and their kind, and NULL is NULL. So `3` and `3.0` agree, and `'3'` and `3`
    don't.

Statements with ORDER BY are compared in order, others as multisets. Statements that read the clock are compared by
row count.

Each arm gets one verdict per statement against the reference (the first arm):

| verdict | meaning |
|---|---|
| `same` | agrees |
| `types` | equal once normalised, not byte for byte (affinity, integer/real) |
| `order` | ordered rows agree as a multiset but not in order: ties, NULL placement |
| `rows` | same row count, different values |
| `rowcount` | different number of rows |
| `changes` | a write changed a different number of rows |
| `error` | only this arm failed |
| `ref-error` | only the reference failed |
| `both-error` | both failed |
| `unstable` | the arm disagreed with itself across its 8 runs |
| `missing` | the process died |
| `untranslated` | no text for the backend's dialect |

The report lists:
- each arm's row counts against the corpus manifest's;
- the verdict counts and errors by message;
- every statement where some arm differs, with the arms grouped by which ones agree: `{sqlite,mine} | {libsql}`
  reads as "libSQL is the odd one out".

### Performance: the methodology (`run.py`, `summarise.py`)

- **Arms in the same run, interleaved.** Builds measured one after another drift with run order. In each round, every
  app's database is copied once per arm, and the arms run it in an order rotated by round and app.
- **Per-statement turns** (`--interleave statement`): the arms take turns statement by statement, each statement in its
  own process, in an order rotated by round, project and statement, so drift within a project's run falls on every arm.
- **An A/A control** (`--aa`): the reference again as `<reference>-aa`. Its ratios to the reference are the run's noise;
  their 2.5th–97.5th percentiles make a band, and summarise counts every other arm's statements outside it.
- **Warm-up apart from steady state.** With exec lines, an execution is warm-up while the arm is still changing paths.
  The rest is steady state. Steady-state ratios need 3 or more steady executions on both sides, because fewer
  measures warm-up. The first execution (prepare plus first run) is reported separately.
- **Instructions, cycles and time.** Instructions are the steady measure for microsecond statements, so decide on
  Linux counters and treat other platforms' wall times as a data point.
- **Geomeans plus distributions:**
  - per-statement ratios of means over rounds, with percentile-bootstrap 95 % intervals over rounds;
  - group geomeans by statement size, tag and app;
  - the ratio distribution's percentiles;
  - the largest losses and wins.
- **Recorded with every result:** host, kernel, CPU, compiler, every backend's build options, version and
  environment, and the corpus and sqlgauntlet commits, in `env.json`.
- **The slow statements apart** (`--heavy-pass [MS]`): only those tagged `heavy` (left out of every other run) and
  those whose manifest `stock_ms_indicative` is MS or more (default 150), with `--reps 3 --budget 0` unless given: the
  pass a full run leaves out or can't afford to repeat.
- **Rows checked in speed runs too:** a statement whose rows differ from the reference's is left out of the timings
  and listed.

### Dialects (`python/sqlgauntlet/dialect.py`)

The corpus is in SQLite's dialect. A backend's `dialect` (in its `.json`) picks how its statements are made:
1. a reviewed override in `corpus/dialects/<dialect>.jsonl`, either `{"id", "sql"}` or `{"id", "untranslatable": why}`;
2. otherwise sqlglot (`pip install sqlglot`), with unsupported constructs raising;
3. otherwise the statement is untranslatable.

How the text is made:
- **Parameters:** SQLite's `?N`, `?`, `:name`, `@name` and `$name` are swapped for markers before translation. They
  come back in the dialect's numbered form (`$1…`), with the statement's parameter list renumbered to match.
- **Identifiers** are lowercased and quoted on both sides (SQLite's are case-insensitive), so names like `user` work.
- **Untranslatable statements** go to `<app>.untranslated.tsv` and are counted by compare, never dropped silently.

**The same data on every engine.** The corpus's data generators are SQLite SQL, so they aren't translated. Each app's
database is built by the SQLite reference, then exported for the other dialect (`python/sqlgauntlet/export.py`):
- **Tables:** each column is typed from both its declared type and the values it actually holds, and primary keys
  are kept. Rowid keys become identity columns.
- **Data:** each table is written as a CSV and loaded through the driver's `load`.
- **Indexes and views:** translated one by one, with inline `UNIQUE` constraints kept so upserts still work. One
  that fails is reported and the rest still run.
- **Left out:** FTS and R-tree tables, which are SQLite features.

The runner keeps each dialect's statements and databases apart (`q-<dialect>/`, `db-<dialect>/`). compare holds
every arm to the SQLite reference.

**What a run against PostgreSQL shows:** sqlglot's translation is mechanical. Statements using SQLite idioms it
doesn't map fail on PostgreSQL, or compute something else:
- `INSERT OR IGNORE`, `GLOB`, `strftime`;
- JSON functions on text columns;
- `COLLATE NOCASE`;
- a parenthesised table in `FROM`.

Some also hit PostgreSQL's stricter grouping. compare reports each one with its message, so you can tell translation
gaps from engine differences. Reviewed overrides in `corpus/dialects/postgres.jsonl` are how coverage grows.

## Workloads (`workloads/`)

Standard benchmarks and fuzzers. Each one pins its inputs by commit and checksum, and downloads or builds what it
can't redistribute.

| workload | what | runs on |
|---|---|---|
| `tpch/make.py [SF]` | TPC-H at scale factor SF (default 0.1). dbgen comes from tpch-kit at a pinned commit, built here. There are two projects: the 22 queries, and small statements (point lookups, short ranges) where per-execution overhead dominates | any backend, as a corpus |
| `clickbench/make.py [rows]` | ClickBench's 43 queries on a checksummed prefix of its dataset (default 1M rows; only the prefix is downloaded), loaded as ClickBench loads SQLite | any backend, as a corpus |
| `speedtest1/run.py <arm>...` | SQLite's own benchmark (`test/speedtest1.c` from the pinned source tree), linked with each backend's own `sqlite3.c` and compile line. Arms are interleaved and verification hashes compared | SQLite-API backends |
| `doomql/make.py [ticks]` | two DOOMQL games (Lukas Vogel's, through our SQLite port; Peter Gostev's, unchanged), MIT: every frame one query (~95,000 VDBE opcodes), at game states built by replaying a fixed input script; frames compared across engines | any SQLite-dialect backend, as a corpus |
| `sqldoom/make.py [--poses N]` | SQLDoom (CedarDB, GPL-2.0: its own directory, fetched, never committed): one 1,561-line query rendering Freedoom's E1M1, the camera and clock as parameters; the map database comes from SQLDoom's WAD loader on Postgres in Docker (`--db` reuses one) | any SQLite-dialect backend, as a corpus |
| `doom-vdbe/run.py <arm>...` | Doom as one VDBE program (Turso's Doom example, rebuilt instruction by instruction as a SQLite Vdbe), run through `sqlite3_step`; every frame's hash must equal Turso's own and the other arms'. The export (Turso's `vdbecc` in Docker, GPL Doom, the shareware WAD) is fetched and built, never committed (`--export` reuses one) | SQLite-API backends on SQLite 3.54 |
| `feedapi/make.py` | the twelve-dollar server challenge's feed API in SQL: the HTTP parser trigger, HMAC-SHA256 as a recursive CTE, the JSON routes, on the challenge's 50k users, 500k posts and 2M likes (`--seed-db` reads an existing seed) | SQLite-dialect backends, as a corpus |
| `sqlancer/` | SQLancer campaigns (NoREC, query partitioning, PQS) through sqlite-jdbc rebuilt from each backend's `sqlite3.c`, with `repro.sh` and `reduce.py` to replay and shrink what it finds | SQLite-API backends; needs a JDK and Maven |

- **TPC-H and ClickBench** produce corpus directories (`build/workloads/<name>`) that `sqlgauntlet-run --corpus` takes.
  Their data loads through the driver's `load`, then `post.sql` (indexes, ANALYZE). Their queries are tagged with
  their shape class from `classes.tsv` (point, range, agg, join, subquery, topn, …), so summarise reports results by
  shape.
- **speedtest1 and SQLancer** find each SQLite-API backend's sources through `build/backends/<name>.src`, which
  `make sqlite` and `make sqlite-api` record.

## Fuzzing (`python/sqlgauntlet/fuzz/`, `fuzz/`, `tests/regressions/`)

Four layers, each running every backend through the same backend table, so that turbo, SQLite, libSQL and Turso are
checked the same way. Keep runs small while iterating (the defaults); long sweeps are for the end, or a box.

| layer | run | what it checks | doesn't cover |
|---|---|---|---|
| Hypothesis properties (`fuzz/props.py`, `fuzz/strategies.py`) | `cd python && ../build/venv/bin/python -m pytest sqlgauntlet/fuzz/props.py` (`SG_FUZZ_BACKENDS`, `SG_FUZZ_PROFILE=full` for 2,000 examples a property) | generated schemas (every affinity, WITHOUT ROWID, partial and expression indexes), awkward data (int64 edges, -0.0, huge doubles, long and non-UTF-8 text, NULLs) and queries (joins, GROUP BY, windows, CTEs, LIKE/GLOB, ORDER BY…LIMIT, parameters); oracles: against SQLite, TLP, NoREC, an index on every column + ANALYZE, 8 hot and 3 fresh runs (an adaptive engine's switches), bound against inlined parameters | writes; statements outside its grammar; non-SQLite dialects |
| corpus mutation (`fuzz/mutate.py`) | `python -m sqlgauntlet.fuzz.mutate corpus build/fuzz/mutated [apps] --per N --seed S`, then `sqlgauntlet-run check --corpus build/fuzz/mutated --arm sqlite --arm <b>` | the real apps' read statements with literals, LIMIT/OFFSET, WHERE (negated, or padded with a term that mustn't change the rows) and DISTINCT varied, on each app's own schema and data | writes; new joins or subqueries |
| libFuzzer (`fuzz/sqlfuzz.c`) | `sh tools/fuzz-libfuzzer.sh <backend> [seconds] [jobs]` (Docker) | crashes, hangs and ASan/UBSan reports in a SQLite-API engine on arbitrary statement text, seeded with the corpus | wrong results (no oracle); Turso and PostgreSQL (not SQLite-API) |
| regressions (`tests/regressions/*.json`, `tests/regressions.py`, in `make test`) | `python3 tests/regressions.py [backends]` | every confirmed finding, minimised, with its seed and per-backend expectation: `same`, or `differs: <why>` for a known difference, so a fix shows up | |

A differential failure is saved to `build/fuzz/failures/` (the newest of an oracle's is Hypothesis's shrunk example).
`tools/fuzz-reduce.py <failure.json>` minimises it further into a regression case. A backend bug goes to its own
repository: turbo's to the sqlite-jit session as a draft PR on turbo.

## Using sqlgauntlet from another project

sqlgauntlet is meant to be depended on, not copied:
- **Python:** `pip install "sqlgauntlet @ git+<url>@<commit>"`, or `pip install -e <checkout>`. That gives
  `sqlgauntlet-run`, `sqlgauntlet-compare`, `sqlgauntlet-summarise` and `sqlgauntlet-prep`, or the `sqlgauntlet`
  package to call.
- **Your own corpus:** `--corpus <dir>` (or `SQLGAUNTLET_CORPUS`) takes any directory in the corpus layout.
- **Your engine, as a plug-in:** build an adapter and your engine into one shared object. Pass it as an arm,
  `--arm mine=so:path/mine.so:KEY=VALUE`. A SQLite-API engine needs no adapter of its own:
  `make sqlite-api NAME=mine SRC=<dir> ENV="KEY=VALUE"`.
- **Your engine, as a library:** link `build/libsqlgauntlet.a` and call `sg_main(&your_table, argc, argv)` from your
  test program.
- **Your own harness, as a reference:** `--arm ref=exe:path/to/program`. Anything with the driver's command line and
  output lines is an arm. `tools/identity.py` then checks that two arms give identical lines.

## Layout

```
include/sqlgauntlet.h   the backend ABI and sg_main
src/                    driver.c (libsqlgauntlet.a), main.c (the CLI)
backends/<name>/        adapter.c, fetch.sh (pinned sources), backend.mk; sqlite also has regexp.c (SQLite's, public domain)
python/sqlgauntlet/     prep, dialect, run, compare, summarise
corpus/                 the corpus (README.md), licences.tsv, UPSTREAM, fetch.sh, tools/ (authoring: run, index, time)
workloads/              TPC-H, ClickBench (as corpora), speedtest1, SQLancer; classes.tsv (query shapes), common.py
tools/                  corpus-split.py, identity.py, check-corpus.py
tests/                  smoke.py, workloads.py (make test)
```

Licence: MIT (`LICENSE`), with two exceptions. The corpus's quoted SQL keeps each application's licence
(corpus/README.md). `workloads/sqldoom/` is GPL-2.0-only, because it is derived from SQLDoom (its COPYING.md). Each
workload's NOTICE.md says what it fetches and under which licence.
