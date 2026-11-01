# Licensing of this folder

SQLDoom ([cedardb/sqldoom](https://github.com/cedardb/sqldoom), © CedarDB) is licensed under the
**GNU General Public License, version 2** (GPL-2.0-only). Freedoom is BSD-3-Clause.

None of SQLDoom's files, and no WAD, are committed here. `fetch.sh` clones SQLDoom at a pinned commit and downloads
Freedoom 0.13.0 (SHA-256-checked) into `src/`, which is git-ignored. Everything generated from them goes to `work/`,
also git-ignored: the loaded database, both renderer versions, and the frames.

Two files here contain fragments of SQLDoom's SQL and turn it into what runs:
- `mkport.py`: the rewrite rules and replacement text for `sql/renderer.sql`;
- `pg/pgload.py`: the rewording of the loader functions and the stage reset for Postgres.

Both are therefore derived from SQLDoom and are distributed under **GPL-2.0-only**, as their `SPDX-License-Identifier`
lines say. The license text is upstream's `LICENSE`, fetched to `src/cedardb-sqldoom/LICENSE`, and is also at
<https://www.gnu.org/licenses/old-licenses/gpl-2.0.txt>.

The other files contain no SQLDoom code and are covered by the repository's license: the drivers, the camera path,
the Docker and Xeon scripts, and the notes.
