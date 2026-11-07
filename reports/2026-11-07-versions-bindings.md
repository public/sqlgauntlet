# SQLite versions and builds, JavaScript bindings and Turso 0.9 on the corpus, 2026-11-07

- **Corpus:** 1,302 statements from 44 apps (the 5 `heavy` ones left out), `sqlgauntlet-run check`: 8 runs per
  statement, every arm against SQLite 3.54.1, and every database built by the reference.
- **Arms:**
  - SQLite 3.35.5 to 3.53.4 (`make sqlite-versions`);
  - 3.54.1 with one option changed (`make sqlite-variants`);
  - Ubuntu 24.04's own libsqlite3 (`make sqlite-system`);
  - `node:sqlite`, `bun:sqlite`, sql.js and `@sqlite.org/sqlite-wasm` (`make js`);
  - Turso 0.8.3 and 0.9.0-pre.2, the latter with and without `generated_columns`.
- **Where:** claude-box (4 vCPU Broadwell) for Turso 0.8.3. The rest ran on Vultr vhp-1c-2gb boxes (AMD Genoa and
  Intel Cascade Lake), in runs of 15 minutes or less. Correctness only: no timings.
- **Also run:** a Hypothesis oracle campaign, `fuzz/props.py` at 2,000 examples per property: differential against
  3.54.1, TLP, NoREC, an index on every column plus ANALYZE, repeat, and bound against inlined parameters. It covered
  the six result-neutral variants and six versions.

## How compatible each arm is

Statements whose result equals SQLite 3.54.1's (`same`), of those that ran:

| arm | ran | same | differs | what differs |
|---|---:|---:|---:|---|
| SQLite 3.51.3, 3.52.0, 3.53.4 | 1,302 | 1,302 | 0 | |
| SQLite 3.49.2, 3.50.4 | 1,302 | 1,298 | 4 | `percentile()` (3.51) |
| SQLite 3.46.1, 3.47.2 | 1,302 | 1,296 | 6 | + phoenix's `unixepoch()` rounding (2) |
| SQLite 3.45.1 / Ubuntu 24.04's libsqlite3 | 1,302 | 1,292 | 10 | + ties, bare columns (4) |
| SQLite 3.40.1 | 1,302 | 1,211 | 91 | + `ORDER BY` in aggregates (3.44), `->` (3.38), `JSONB`, `concat`, `octet_length` |
| SQLite 3.35.5 | 1,302 | 1,087 | 215 | + `STRICT` (3.37), `unixepoch()` (3.38); JSON was missing, a harness bug (below) |
| stat4, sorter-refs, null-trim, O3 | 1,302 | 1,302 | 0 | |
| O0 | 855 | 854 | 1 | `percentile()` in the last bit: FMA (below) |
| no-autoindex | 303 | 303 | 0 | (run cut short) |
| dqs0 | 1,302 | 1,293 | 9 | double-quoted string literals in 6 apps |
| fkeys | 1,302 | 1,298 | 4 | foreign keys enforced: 3 writes fail, 1 schema mismatch |
| `bun:sqlite` (Bun 1.4.4, SQLite 3.53.4) | 1,302 | 1,295 | 7 | integral reals rebound as integers (4), `regexp()` |
| `node:sqlite` (Node 26.13, SQLite 3.54.0) | 1,302 | 1,285 | 17 | its defaults: DQS off (9), foreign keys on (4); `regexp()` |
| `@sqlite.org/sqlite-wasm` 3.54.1-build1 | 1,302 | 1,274 | 28 | DQS=0, no FTS4, integral reals rebound as integers (4), **a binding bug** (below) |
| sql.js 1.15.2 (SQLite 3.54.0) | 1,302 | 1,210 | 92 | no FTS5 (33) or `percentile()`, BigInt parameters bound as text |
| Turso 0.8.3 | 1,075 | 899 | 176 | unsupported SQL (137), `changes()` (7), rows (23); 6 apps over 600 s |
| Turso 0.9.0-pre.2 | 863 | 791 | 72 | fixes 30 of 0.8.3's errors; refuses every file with an FTS table |

`ran` excludes apps whose database the arm couldn't open, or that went past the time limit. 3.37.2 ran on 692
statements only and isn't in the table. The Hypothesis oracles found no failure in any of the six result-neutral
variants.

## Findings

Ranked by severity. None has been reported upstream yet.

1. **Turso: row-value comparisons aren't used as index range bounds, which makes atuin's search effectively hang.**
   - Versions: 0.8.3 and 0.9.0-pre.2.
   - On the corpus: `atuin.search.fuzzy_global_dedup` takes 0.56 s in SQLite and over 300 s in Turso.
   - Shape: `NOT EXISTS (SELECT 1 FROM h f2 WHERE f2.c = f.c AND (f2.ts, f2.id) > (f.ts, f.id))` with an index on
     `(c, ts)`. SQLite seeks `c=? AND ts>=?`; Turso tests the row value on every row of the `c` group, so the time is
     quadratic in the group size. atuin's largest group has 27,008 rows.
   - Reproducer: 10,000 rows in 5 groups take 0.16 s in SQLite and over 30 s in Turso.
   - Upstream: related to tursodatabase/turso#10977, which covers single-element row values only.
2. **sqlite-wasm: an integral JS number outside the int64 range is bound as an integer, and the value is corrupted.**
   - Version: `@sqlite.org/sqlite-wasm` 3.54.1-build1; the same code is on SQLite trunk as of 2026-11-02.
   - Results: DBL_MAX binds as 0, 2^63 as INT64_MIN, 1e19 as -8446744073709551616. node, bun and sql.js bind all
     three as REAL.
   - On the corpus: signal-desktop's media queries use DBL_MAX as their upper bound and return 0 rows instead of 30.
   - Cause: oo1 `Stmt.bind()` takes `bind_int64` whenever `Number.isInteger(val)` holds, with no `bigIntFits64` check
     (the result path has one).
3. **Turso: `changes()` adds 2 x the rows a window function reads.**
   - Versions: 0.8.3 and 0.9.0-pre.2.
   - Reproducer: `INSERT INTO o SELECT x FROM (SELECT row_number() OVER () AS x FROM d)` with 3 rows reports 9.
   - It happens anywhere the statement has a window function: top-level, a subquery, a CTE, a view, or
     `DELETE ... IN (...)`.
   - On the corpus: 6 of Turso's 7 change-count differences. netalertx reports 184,142 instead of 6, through a view
     using `ROW_NUMBER()` over 92,068 events.
4. **Turso 0.8.3: a file with an FTS5 table loads only part of its schema, so writes break UNIQUE and corrupt
   indexes.** This is tursodatabase/turso#9282, fixed for 0.9. It is the 7th change-count difference (calibre), and
   `integrity_check` confirms the corruption. 0.9.0-pre.2 refuses every statement on such a file instead (#10033, open):
   joplin, signal-desktop, synapse, zotero and the other FTS apps can't be opened.
5. **sql.js: a BigInt parameter is bound as TEXT.** This is known (sql-js/sql.js#341). atuin's `get_dups` returns 0 rows
   instead of 27,155.

## Not bugs: unspecified, or a deliberate change

- **Binding number rules:** JavaScript has one number type, so a REAL whose value is integral comes back as an integral
  JS number. bun, sqlite-wasm and sql.js (within int32) then bind it as INTEGER, and `x / ?` becomes integer division:
  4 phoenix statements bind `1000000.0` and get 357 instead of 357.333. `node:sqlite` binds every number as REAL.
- **`node:sqlite`'s defaults:** foreign keys on and DQS off, unlike SQLite's. So do the dqs0 and fkeys variants:
  datasette, firefox, netalertx, abs, calibre, syncthing, ha and screenpipe each have statements that depend on SQLite's defaults.
- **Real-to-text before 3.51:** a REAL stored in a TEXT column gets 15 significant digits (`6.44e+16`), which doesn't
  round-trip; 3.54 writes `64399999999999952.0`. Text comparisons on such columns then differ. This accounts for every
  differential-oracle failure on 3.40.1, 3.47.2 and 3.50.4.
- **`sum()` before 3.43:** the float rounding, and whether an integer sum overflows partway, depend on row order, so an
  index changes them. This accounts for all 50 index-oracle failures on 3.35.5. 3.43's Kahan summation removed the
  float half.
- **FMA:** `-march=native` lets GCC contract `percentile()`'s interpolation into a fused multiply-add, which changes
  the last bit (O0 and `-ffp-contract=off` agree with each other, not with -O2). Strict hashes of builds for CPUs with
  and without FMA can therefore differ.
- **Ties under `LIMIT`, and bare columns in `GROUP BY`** (tautulli, navidrome, firefox `urlbar.input_history`): the
  rows an engine picks are unspecified. Turso doesn't implement SQLite's documented bare-column-with-`max()` rule
  (tursodatabase/turso#5545).

## Harness bugs found and fixed

- **Stale WAL** (#8): run.py copied each project's database over `tmp/c.db` but kept `c.db-wal`. A WAL is replayed onto
  any file, so a WAL left by a killed Turso process, or by Turso's normal close, leaked into the next project.
- **Timeouts** (#8): one project past the time limit failed the whole arm.
- **Option order** (#8): run.py rejected projects given after the options.
- **NUL in an inlined literal** (#11): `fuzz/strategies.py`'s `lit()` quoted text that holds a NUL, which SQL can't
  express, so `test_params` failed with no engine fault.
- **JSON1 before 3.38** (#12): the version arms first lacked JSON, which distributions enable.
- **Parameter binding in the JS driver** (#13): it bound parameters a statement doesn't use, and put a named
  parameter's value at the wrong index.

## Not done

- SQLite 3.37.2 ran on half the corpus; 3.35.5 and 3.37.2 need rerunning with JSON1 (#12).
- `no-autoindex` and O0 ran on part of the corpus. Both pass every Hypothesis oracle.
- Turso 0.9.0-pre.2 is missing the apps that ran past the time limit (atuin, grocy, navidrome, netalertx, phoenix,
  screenpipe) and the apps with FTS tables.
- libSQL: its newest bundle, at main a1d031c, is byte-identical to the pinned one (0.2.3, SQLite 3.47.0), so there is
  nothing newer to test.
- DuckDB and MySQL through the dialect layer: not started.
- SQLancer through sqlite-jdbc needs a JDK, which claude-box doesn't have.
