# sqlgauntlet

A correctness and performance harness for SQL engines. It runs a corpus of real application statements through
several backends and reports two things: **where the backends disagree** (not just a pass rate), and **how fast each
one is** against a reference, using a methodology that separates warm-up from steady state.

It grew out of [cursed-sqlite-turbo](https://github.com/public/cursed-sqlite-turbo)'s bench, a JIT/specialising layer
over SQLite, and keeps that bench's file formats and measurement rules. Its backends today:

| backend | kind | status |
|---|---|---|
| `stock` | SQLite 3.54.1, the reference | runs |
| `turbo` | cursed-sqlite-turbo's native build (`QC_SERVE=1`) | runs; identical results to turbo's own bench |
| `libsql` | libSQL 0.2.3 (SQLite 3.47.0 fork), its bundled amalgamation | runs |
| `turso` | Turso 0.8.3 (formerly Limbo, a Rust rewrite), over its C ABI | adapter written; needs `scripts/fetch-turso.sh build` (Rust) |
| DuckDB, PostgreSQL, MySQL | other dialects, through `dialect.py` | interface only (below) |

## Quick start

```bash
make backends                        # build/sqlgauntlet, build/libsqlgauntlet.a, build/backends/{stock,turbo,libsql}.so
```

```bash
sh corpus/fetch.sh                   # the corpus apps that can't be committed here (see Corpus)
```

```bash
cd python && python3 -m sqlgauntlet.run check --arm stock --arm turbo --arm libsql
```

```bash
cd python && python3 -m sqlgauntlet.run speed --arm stock --arm turbo --rounds 3 --exec-lines
```

`TURBO=<checkout>` points the turbo backend at a cursed-sqlite-turbo tree (default `~/Documents/my-code/cursed-sqlite-jit`).
Results go to `build/run/` (`--out`): one TSV per arm and mode, `env.json`, and the Markdown report on stdout.

## Design

```
 corpus/ (projects/<app>/{schema,data,queries}.sql, manifest*.jsonl)
    │  python/sqlgauntlet/prep.py   (dialect.py for non-SQLite dialects)
    ▼
 <app>.build.sql, <app>.q        flat files, one pair per app
    │  python/sqlgauntlet/run.py    arms, interleaving, env.json
    ▼
 sqlgauntlet --backend X.so <mode>  src/driver.c over include/sqlgauntlet.h
    │                            (or any program with the same command line: an "exe" arm)
    ▼
 <arm>.check.tsv, <arm>.<hot|prep>.<round>.tsv   C / N / T / E lines
    │  compare.py, summarise.py
    ▼
 Markdown: disagreements, timings
```

### The backend interface (`include/sqlgauntlet.h`)

A backend is one table of C functions, `sg_backend`: `open`, `close`, `exec` (scripts), `prepare`, `param_count`,
`param_index`, `bind`, `step`, `column_count`, `column`, `reset`, `finalize`, `changes`, `errmsg`. The optional
hooks are `exec_begin` and `exec_stats` (per-execution facts the driver can't see), `counter_names` and `explain`.
Values cross the ABI as `sg_value`, SQLite's five storage classes with the same numbers.

- **In-process engines** fill the table directly. `adapters/sqlite.c` serves any library with SQLite's C API
  (stock, turbo, libSQL). `adapters/turso.c` serves Turso's `turso.h`.
- **Backends are shared objects** that export `sqlgauntlet_backend_v1()`. The CLI loads one with `--backend` (dlopen,
  `RTLD_LOCAL`), so several builds of SQLite never share symbols. Each backend has its engine linked in and only
  that one symbol exported.
- **"served"** is the one notion that comes from turbo, kept as an optional, generic field. It says whether the
  backend's fast path ran this execution. turbo's adapter reads `SQLITE_STMTSTATUS_VM_STEP == 0`. A backend without
  one reports -1.
- **Networked engines (client adapters)** fill the same table with a client: `open` connects, `prepare` is a
  server-side prepare, `step` fetches the next row. Their `exec_stats` returns `engine_ms`, the server's own measure
  of the execution: PostgreSQL's `pg_stat_statements.total_exec_time` delta or `EXPLAIN (ANALYZE, TIMING)`;
  MySQL's `performance_schema.events_statements_history.TIMER_WAIT`. The driver times the wall span as for any
  backend and prints `engine-ms` on each E line, so `wall − engine` is the protocol and client cost. That split is
  in the interface and the E lines. No client adapter is written yet.

### The driver (`src/driver.c`, `libsqlgauntlet.a`)

The modes are those of turbo's `bench/corpus/corpus.c`, with the same output lines, byte for byte for the same rows.
`tools/identity.py` holds it to that: on the full corpus, every C line of sqlgauntlet's stock and turbo arms equals
those of turbo's own driver built over the same engines. There is one deliberate difference: a statement tagged
`wallclock` isn't called `unstable` when the clock ticks between its 8 runs, because its values follow the clock.
turbo's `compare.py` and `summarise.py` read sqlgauntlet's output, and sqlgauntlet's reports read turbo's.

| mode | what | lines |
|---|---|---|
| `build` | the app's database from its build script | |
| `check` | each statement 8 times: 6 on one handle, then 2 freshly prepared; runs must agree with each other | `C id status rows changes hash msethash served/runs error`, `N id nhash nmsethash` |
| `hot` | prepared once, then 1 + reps executions (reset between), until `budget-ms` is spent | `T id rows hash first-ms median-ms mean-ms runs instr cycles served/runs error` |
| `prep` | every execution prepares, binds, steps and finalizes | the same T line |
| `rows` | one statement's normalised rows (for reading a disagreement) | `R` |

- **Writes:** each write runs inside `BEGIN … ROLLBACK`, outside the timed span. Reads run in autocommit.
- **Parameters:** literals are evaluated by the backend itself (`SELECT <literal>`) and bound by name, or by position
  for anonymous `?`. This is the code path that once caught turbo's bench binding NULL for anonymous `?` and binding
  only 64 parameters.
- **Counters:** on Linux, user-mode instructions and cycles come from `perf_event_open` over the timed spans
  (`perf_event_paranoid ≤ 2`); elsewhere they are -1.
- **E lines:** `SG_EXEC=1` prints one per execution: `E id k served ms instr cycles engine-ms`.

### Correctness: normalisation and verdicts (`python/sqlgauntlet/compare.py`)

Every result is hashed twice:
- **Strict** (`hash`, `msethash`): each value's storage class and bytes. This is turbo's hash: a JIT must match
  SQLite exactly.
- **Normalised** (`nhash`, `nmsethash`):
  - **Numbers:** integers and floats are one kind. A float with an exact integer value within 2^53 counts as that
    integer; other floats are rounded to `SG_DIGITS` significant digits (default 12).
  - **Text and blobs** keep their bytes and their kind, and NULL is NULL. So `3` and `3.0` agree, and `'3'` and `3`
    don't.

Statements with ORDER BY are compared in order. Every other statement is compared as a multiset. Statements that
read the clock (`wallclock`) are compared by row count.

Each arm gets one verdict per statement against the reference:

| verdict | meaning |
|---|---|
| `same` | agrees |
| `types` | equal once normalised, not byte for byte: an affinity or integer/real difference |
| `order` | ordered rows agree as a multiset but not in order: ties or NULL placement |
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
- the verdict counts per arm;
- errors by message;
- every statement where some arm differs, with the arms grouped by which ones agree with each other: so
  `{stock,turbo} | {libsql}` reads as "libSQL is the odd one out".

### Performance: the methodology (`run.py`, `summarise.py`)

The rules are turbo's, learnt the hard way:
- **Arms in the same run, interleaved.** Builds measured one after another drift with run order. In each round,
  every app's database is copied once per arm, and the arms run it in an order rotated by round and app. No arm is
  always first on a cold cache.
- **Warm-up apart from steady state.** With E lines, an execution is warm-up while the arm's served flag is still
  changing. That's turbo's adapt trying the interpreter and its plan before it settles. The rest is steady state.
  Steady-state ratios need 3 or more steady executions on both sides, because fewer measures warm-up. The first
  execution (prepare plus first run) is reported separately.
- **Instructions, cycles and time.** Instructions are the steady measure for microsecond statements, so use Linux
  for decisions.
- **Geomeans plus distributions:**
  - per-statement ratios of means over rounds, with percentile-bootstrap 95 % intervals over rounds;
  - group geomeans by statement size, tag and app;
  - the ratio distribution's percentiles;
  - the largest losses and wins.
- **Recorded with every result:** box, kernel, CPU, compiler, every backend's build options and version, and the
  corpus and sqlgauntlet commits, in `env.json`.
- **Row counts and hashes checked in speed runs too.** A statement whose rows differ from the reference's is left
  out of the timings and listed.

### Dialects (`python/sqlgauntlet/dialect.py`)

The corpus is SQLite SQL. A backend's `dialect` picks how its text is made:
1. a reviewed override in `corpus/dialects/<dialect>.jsonl`, either `{"id", "sql"}` or `{"id", "untranslatable": why}`;
2. otherwise sqlglot (`pip install sqlglot`), with unsupported constructs raising;
3. otherwise the statement is untranslatable.

Untranslatable statements are written to `<app>.untranslated.tsv` and counted by compare. They are never dropped
silently. Schemas and data scripts go through the same path per app.

This is the layer DuckDB, PostgreSQL and MySQL need. They are not done yet: the DuckDB adapter (its C API's prepared
statements and result chunks) is the next step.

## Using sqlgauntlet from another project

sqlgauntlet is meant to be depended on, not copied:
- **Python:** `pip install "sqlgauntlet @ git+<url>@<commit>"`, or `pip install -e <checkout>` (`pyproject.toml`).
  That gives `sqlgauntlet-run`, `sqlgauntlet-compare`, `sqlgauntlet-summarise` and `sqlgauntlet-prep`, or the `sqlgauntlet` package
  to call.
- **Your own corpus:** `--corpus <dir>` or `SQLGAUNTLET_CORPUS` takes any directory in the corpus layout. turbo passes
  its own `bench/corpus`.
- **Your own engine, as a plug-in:** compile `adapters/sqlite.c` (or your own adapter) with your library into a shared
  object, and pass it as an arm: `--arm mine=so:path/mine.so:QC_SERVE=1`.
- **Your own engine, as a library:** link `build/libsqlgauntlet.a` and call `sg_main(&your_table, argc, argv)` from
  your test program. No dlopen is needed.
- **Your own program:** `--arm ref=exe:path/to/program`. Anything with the driver's command line and output lines is
  an arm. This is how `tools/identity.py` holds sqlgauntlet to turbo's own `corpus.c`.

A project pins one sqlgauntlet commit, the way it pins SQLite.

## Corpus

44 apps and 1,307 statements (1,302 without the 5 tagged `heavy`, which runs leave out unless `--heavy`), from
cursed-sqlite-turbo's `bench/corpus` at the commit in `corpus/PIN`. Every statement names its app's repository, commit
and file:line. Each app's licence decides where its SQL lives (`corpus/licences.tsv`):

| class | licences | apps | statements | where |
|---|---|---:|---:|---|
| vendor | MIT, Apache-2.0, BSD-3-Clause, MPL-2.0 | 20 | 580 | committed in `corpus/projects/`, each with the app's LICENSE and a NOTICE.md |
| fetch | GPL-2.0/3.0, AGPL-3.0, EUPL-1.2, Elastic-2.0, n8n's Sustainable Use License | 24 | 727 | `corpus/fetch.sh` copies them from cursed-sqlite-turbo at the pinned commit |

- **Our own work:** the synthetic data (`data.sql`) and `meta.json` are cursed-sqlite-turbo's own, under MIT.
- **MPL-2.0 files** (firefox-places, syncthing) stay under the MPL, with its text beside them as `LICENSE`.
- **screenpipe** is taken at its last MIT-licensed commit (892199f). It has since moved to a licence that grants no
  right to redistribute.
- **Fetching today:** the pinned commit is not yet pushed, and cursed-sqlite-turbo is still private. So
  `corpus/fetch.sh` works only from a local checkout (`sh corpus/fetch.sh <checkout>`, or `TURBO_REPO`). Until both
  are published, the vendored 580 statements are what anyone else can run. Never pin a turbo commit older than
  0c63afa: earlier history carries screenpipe SQL under its newer licence.
- **Regenerating:** `tools/corpus-split.py <turbo-repo> <commit>` rebuilds `corpus/` and refuses any licence it
  hasn't been told how to class.

## Layout

```
include/sqlgauntlet.h      the backend ABI and sg_main
src/driver.c            the driver (libsqlgauntlet.a); src/main.c the CLI
adapters/               sqlite.c (any SQLite-API library), turso.c, regexp.c (SQLite's ext/misc, public domain)
python/sqlgauntlet/        prep, dialect, run, compare, summarise
corpus/                 vendored apps, licences.tsv, PIN, fetch.sh
scripts/                pinned fetches: SQLite 3.54.1, libSQL, Turso
tools/                  corpus-split.py, identity.py
reports/                the reports kept with the repository
```

Licence: MIT (`LICENSE`). The corpus's quoted SQL keeps each app's licence, as above.
