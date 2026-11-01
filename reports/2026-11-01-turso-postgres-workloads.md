# Turso, PostgreSQL and the new workloads, 2026-11-01

- **Machine:** Apple M-series, macOS.
- **Engines:**
  - SQLite 3.54.1 (`sqlite`, the reference);
  - cursed-sqlite-turbo built from a checkout (`turbo`);
  - libSQL 0.2.3, which is SQLite 3.47.0 (`libsql`);
  - Turso 0.8.3 through its C ABI (`turso`);
  - PostgreSQL 17.11 through libpq (`postgres`), with the statements translated by sqlglot 30.27.0 and every app's
    data exported from the SQLite build.
- **What was run:** correctness only (`sqlgauntlet-run check`, 8 runs per statement). There were no timing runs.

## The corpus (1,302 statements, 44 apps)

| arm | same | types | order | rows | rowcount | changes | error | missing | untranslated |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| turso | 956 | | 11 | 27 | | 8 | 151 | 149 | |
| postgres | 716 | 17 | 27 | 26 | 22 | 1 | 472 | | 11 |

(Postgres also had 10 statements whose runs disagreed with each other.)

**Turso:**
- **Its own errors (151),** by message:
  - parenthesised FROM subqueries (21);
  - ORDER BY inside aggregates (10);
  - ON CONFLICT targets it can't match (7);
  - UPSERT and REPLACE on WITHOUT ROWID tables (5);
  - scalar subqueries in some positions;
  - FTS tables.
- **Missing (149):** five apps' databases wouldn't open, because they use generated columns. With
  `SG_TURSO_FEATURES=generated_columns`, three of them open, and 61 of their 89 statements agree. Stored generated
  columns (goatcounter, mlflow) are not supported at all.
- **Different rows (27):** some are real semantic gaps. `atuin.list.unique_global` uses `GROUP BY` with
  `HAVING max(timestamp)` and bare columns; SQLite returns each group's row with the max, and Turso returns other rows.
  Others are ties cut by `LIMIT`, where either answer is legitimate.

**PostgreSQL:** most of the 472 errors are SQLite idioms that sqlglot doesn't translate. By message:
- `COLLATE NOCASE` (35);
- FTS `MATCH` (30);
- `INSERT OR …` (24);
- SQLite-only tables (`page_stat`, 17);
- a parenthesised table in FROM (15);
- an integer used as a boolean (13);
- PostgreSQL's stricter `GROUP BY` (24 or more).

The 23 statements that return a different row count from the manifest are mostly case-sensitive `LIKE`, GUIDs as
blobs, and date arithmetic. Reviewed overrides in `corpus/dialects/postgres.jsonl` would grow the coverage.

**Harness bugs these runs found and fixed:**
- identifiers' case;
- `main.` prefixes;
- generated columns dropped from the export;
- SQLite pragmas sent to PostgreSQL;
- an unordered statement's 8 runs compared in order;
- two runs sharing one PostgreSQL database;
- the PostgreSQL loader's placeholders;
- `changes` compared after reads.

## The workloads

| workload | sqlite | turbo | libsql | turso |
|---|---|---|---|---|
| TPC-H SF0.1 (22 queries + 18 small statements) | reference | | 40/40 same | |
| ClickBench, 300k rows (43 queries) | 43 ok | | | |
| DOOMQL, 2 games × ticks 0 and 60 (8 frames) | reference | 8/8 same | 8/8 same | can't open: ORDER BY in aggregates (in the renderer's views) |
| SQLDoom, Freedoom E1M1 (6 poses) | reference | 6/6 same | 6/6 error: `no such column: tv0.wanted_rotation` (3.47) | |
| feedapi, the twelve-dollar challenge's API in SQL (8 statements) | reference | 8/8 same | 8/8 same | can't run: views referencing temp tables |
| doom-vdbe, Doom as one VDBE program (30 frames) | 30/30 equal to Turso's own hashes | 30/30, equal to sqlite's | skipped: the program is built for 3.54's opcodes | |
| speedtest1 (`--size 10`) | reference | | same verification hash | |

**Notes:**
- **feedapi's HMAC-SHA256** recursive CTE reproduces the seed token's signature exactly.
- **SQLDoom** needs `SQLITE_MAX_EXPR_DEPTH` above the default 1,000. The SQLite-API backends now build with 100000;
  that changes only which queries are refused.
- **The CLI extensions:** the SQLite-API backends now include the sqlite3 CLI's `regexp()`, `generate_series` and
  `base64()`.
- **Not run:**
  - SQLancer's Java build (no Maven here);
  - the doom-vdbe export (Turso's `vdbecc` in Docker: an existing export was used);
  - SQLDoom's map loader (Postgres in Docker: an existing E1M1.db was used);
  - feedapi's seed generator (`--seed-db` read the challenge's existing feed.db).
