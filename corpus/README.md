# The corpus

Real statements from 44 applications: 1,307 in all, or 1,302 without the 5 tagged `heavy`. Each application comes
with its schema, deterministic synthetic data, the statements' parameters, and the row counts they return. Any
directory in this layout works with `--corpus`. A project with its own statements writes them the same way.

## Layout

```
projects/<app>/
  meta.json      provenance: repo, commit (sha), licence, the app's connection settings ("pragmas"), "analyze"
  schema.sql     the DDL the statements need, from the app's migrations or schema
  data.sql       deterministic synthetic data in plain SQL; reads the temp table _cfg(scale), default 1
  queries.sql    the statements, one annotated block each (below)
  LICENSE        the app's licence, unchanged; NOTICE.md: what is quoted from it and what was changed
manifest.jsonl   one line per statement: id, app, licence, source file:line, tags, rows and changes at scale 1, sql
                 (here split into manifest.vendored.jsonl and the fetched manifest.fetched.jsonl)
```

`queries.sql` blocks:

```sql
-- @id memos.list_memos.home_feed            unique, <app>.<area>.<what>
-- @src store/db/sqlite/memo.go:118-246      path:line at the pinned commit
-- @tags hotpath,dynamic                     manual tags; the manifest adds automatic ones
-- @param ?1 7                               one per parameter: its name, then an SQL literal
SELECT ...;
```

- **Parameters:** written as SQL literals, and each backend evaluates them itself (`SELECT <literal>`), so every
  engine binds its own reading of the value. Positional parameters are `?N`; named ones keep their `:`, `$` or `@`.
- **Tags the harness acts on:**
  - `orderby` and `topn`: rows are compared in order; otherwise as a multiset.
  - `read`: anything else runs inside `BEGIN … ROLLBACK`.
  - `wallclock`: the statement reads the clock or `random()`, so it is compared by row count.
  - `heavy`: 1 s or more at scale 1; left out unless `--heavy`.
  - `expect-empty`: 0 rows is the right answer.
- **Data:** `data.sql` uses no `random()`. Its values are hashes of a row counter, and row counts scale with `_cfg`.
  At scale 1 a database builds in seconds.
- **Dialect:** SQLite's. Another engine's text comes from `dialects/<dialect>.jsonl` overrides or a translator
  (`python/sqlgauntlet/dialect.py`). A statement with no text for a dialect is reported as untranslated.

## Where it comes from, and licensing

The statements were collected for [cursed-sqlite-turbo](https://github.com/public/cursed-sqlite-turbo). On 2026-11-01
this directory became their home: it was imported from turbo's `bench/corpus` at the commit in `UPSTREAM`, and changes
are now made here. Every statement names its application's repository, commit and file:line. The quoted SQL keeps its
application's licence (`licences.tsv`):

| class | licences | apps | statements | where |
|---|---|---:|---:|---|
| vendor | MIT, Apache-2.0, BSD-3-Clause, MPL-2.0, GPL-2.0/3.0, AGPL-3.0, EUPL-1.2 | 42 | 1,245 | committed in `projects/`, each with its LICENSE and NOTICE.md |
| fetch | Elastic-2.0 (phoenix), n8n's Sustainable Use License | 2 | 62 | `sh corpus/fetch.sh` copies them from turbo's history at the pinned commit |

- **Licences per directory:** each `projects/<app>/` stays under its application's licence, not this repository's
  MIT licence. Its NOTICE.md says what is quoted and what was changed. For the GPL, AGPL and EUPL apps, `schema.sql`
  holds only the `CREATE` statements the queries need, and `tools/fetch.sh` fetches the original files at the pinned
  commit.
- **Why two apps are fetched:** both licences restrict redistribution. Elastic-2.0 forbids offering the software as a
  managed service, and n8n's licence allows only free, non-commercial distribution. So their SQL isn't committed to
  this repository, which anyone may use commercially.
- **MPL-2.0 files** (firefox-places, syncthing) stay under the MPL.
- **screenpipe** is taken at its last MIT-licensed commit.
- **The synthetic data** (`data.sql`) and `meta.json` are the upstream's own, under MIT.
- **Fetching today:** turbo's repository is still private. Until it is published, `fetch.sh <local clone>` is the
  only way to get the two fetch-class apps. Their files must stay in turbo's history at the pinned commit after turbo
  drops `bench/corpus`.
- **Maintaining it:** `tools/run.py` runs every statement and records row counts, `tools/index.py` rescores and
  regenerates STATS.md, and `tools/time.py` records indicative timings. These tools come from the upstream unchanged
  and write a single `manifest.jsonl`; split it back into the vendored and fetched halves by licence class.
  `tools/corpus-split.py` (in the repository's `tools/`) rebuilds this directory from a turbo commit, and refuses any
  licence it hasn't been told how to class.
