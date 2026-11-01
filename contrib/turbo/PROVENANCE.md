# Provenance

Imported on 2026-11-01 from [cursed-sqlite-turbo](https://github.com/public/cursed-sqlite-turbo). It is a clean import
of the files as they stood, without their history. That history is in turbo's repository, on the unmerged branch
`claude/bench-110a4d` at **4affcbd** ("corpus run.sh arm margin098: serve with QC_ADAPT_MARGIN=0.98"), on turbo's
rewritten history. The author is Alex Stapleton, the same as this repository's. The files were not changed on import.

| here | there (at 4affcbd, unless noted) |
|---|---|
| `contrib/turbo/bench/` | `bench/`, without `bench/corpus/`'s corpus files (below) |
| `contrib/turbo/bench/corpus/` | `bench/corpus/`'s harness only: `corpus.c`, `run.sh`, `prep.py`, `compare.py`, `summarise.py`, `cg.py`, `fast.py`, `pick-losers.py`, `losers.tsv`, `regexp.c`, `repros/` |
| `contrib/turbo/sqlancer/` | `tests/sqlancer/` |
| `contrib/turbo/BENCH.md` | `docs/BENCH.md` (the apparatus's documentation and its results) |
| `contrib/turbo/explorer/` | `bench/corpus/explorer/` at **0c63afa** (the corpus pin) |
| `corpus/tools/`, `corpus/{INDEX,STATS,SURVEY}.md`, `corpus/scores.json` | `bench/corpus/tools/`, `bench/corpus/{INDEX,STATS,SURVEY}.md`, `bench/corpus/scores.json` at **0c63afa** |
| `corpus/projects/`, `corpus/manifest.*.jsonl` | `bench/corpus/projects/`, `bench/corpus/manifest.jsonl` at **0c63afa**, split by licence (`tools/corpus-split.py`) |

The corpus files came from the corpus pin, not from 4affcbd. The branch's copies predate the licence work: per-app
LICENSE and NOTICE files, and screenpipe at its MIT commit.

These scripts keep turbo's layout: they find `qc/`, `scripts/` and `third_party/` relative to the repository root.
`contrib/turbo/tree.sh` gives them that layout by laying them over turbo at the pinned commit (`contrib/turbo/TURBO`).
