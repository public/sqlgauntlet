# workloads/doom-vdbe: licensing

- **This repository's own (MIT):** `doomsql.c` (the program rebuilt as a SQLite Vdbe and run through `sqlite3_step`),
  `vdbe2sqlite.rs` (the exporter, built against Turso's `vdbecc`), `run.py` and the scripts. They were written in
  cursed-scratchpad's `explore/doom-jit`.
- **Fetched by `fetch.sh` and never committed:**
  - [tursodatabase/turso-vdbe-doom-example](https://github.com/tursodatabase/turso-vdbe-doom-example) at `52be17f`,
    with its `vdbecc` compiler and Doom's LLVM IR (doomgeneric, GPL-2.0);
  - id Software's shareware `DOOM1.WAD`, under its own licence, SHA-256-checked.

  An export (`prog.txt`, `ram.bin`) contains both, so it stays under `build/`.
