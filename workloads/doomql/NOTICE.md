# workloads/doomql: licensing

- **Two games, both under the MIT licence, fetched by `fetch.sh` and not committed:**
  - [cedardb/DOOMQL](https://github.com/cedardb/DOOMQL), © 2025 Lukas Vogel, at `8d6c2fc`;
  - [petergpt/doomql](https://github.com/petergpt/doomql), © Peter Gostev, at `a2b40b6`.
- **The SQLite port of the first:** `port/*.sql` and `mkport.py` are derived from DOOMQL's SQL (PORT.md lists every
  rewrite). They are distributed under its MIT licence, © 2025 Lukas Vogel. They were written in cursed-scratchpad's
  `explore/doomql-jit` (commit 126880a1), where the frames were checked byte for byte against Postgres 16.
- **`mkpeter.py` and `make.py`** are this repository's own (MIT). They contain none of either game's code.
