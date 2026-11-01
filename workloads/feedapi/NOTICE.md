# workloads/feedapi: licensing

- **The challenge:** [arjaythedev/twelve-dollar-server-challenge](https://github.com/arjaythedev/twelve-dollar-server-challenge)
  (MIT, © 2026 arjaythedev and the challenge contributors) defines the feed API, its schema and its seed data.
  `fetch.sh` fetches them at a pinned commit; none of them is committed here.
- **`sql/`:** the entry "SQL, served by the sqlite3 CLI" (`submissions/sql-sqlite3cli-public`, © 2026 Alex
  Stapleton, MIT): the web application written in SQL, its HTTP parser trigger, its routes, and the generator of its
  HMAC-SHA256 recursive CTE. It is copied here unchanged from cursed-scratchpad's `challenge-bash-unix` at c1fc6fd.
