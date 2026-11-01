# What cursed-sqlite-turbo can delete now that sqlgauntlet has it

This is for a later turbo PR. Nothing has been deleted or force-pushed in turbo.

## The branch `claude/bench-110a4d` (4affcbd)

None of it needs to merge into turbo's `main`. Everything the branch adds over `main` is in `contrib/turbo/` here:

```
bench/Dockerfile bench/docker.sh bench/lib.sh bench/build-sqlite.sh bench/qc_serve.c bench/qrun.c
bench/compare.py bench/parity.py bench/winloss.py bench/classes.tsv
bench/small/  bench/speedtest1/  bench/tpch/  bench/clickbench/  bench/xeon/
bench/corpus/{corpus.c,run.sh,prep.py,compare.py,summarise.py,cg.py,fast.py,pick-losers.py,losers.tsv,regexp.c}
bench/corpus/repros/
tests/sqlancer/
docs/BENCH.md
```

The branch can be retired once nobody runs its scripts from turbo's tree. Its edits to `docs/RESULTS.md` are turbo's
own results and stay a turbo matter.

## turbo `main` (4dbc9f1)

| path | removable | condition |
|---|---|---|
| `bench/corpus/tools/`, `bench/corpus/explorer/` | yes | now `corpus/tools/` and `contrib/turbo/explorer/` here |
| `bench/corpus/{INDEX,STATS,SURVEY,README}.md`, `scores.json`, `.gitignore` | yes | now `corpus/` here (`corpus/README.md` replaces the README) |
| `bench/corpus/projects/`, `bench/corpus/manifest.jsonl` | yes, with care | see below |
| `bench/qbench.c`, `bench/stockcost.c` | **no** | turbo's own benches against qc's API, built by turbo's Makefile (`make bench`, `make stock-bench`) |
| `tests/corpus.c` | **no** | turbo's query-flavour test (`make corpus`), not the corpus driver |

**The corpus files, with care:**
- **Fetching keeps working.** sqlgauntlet's `corpus/fetch.sh` copies the 24 copyleft or source-available apps out of
  turbo's history at the pinned commit (0c63afa). Deleting them from turbo's tip doesn't break that, as long as the
  history is kept.
- **Re-pinning to a newer corpus is what breaks.** That would need the 24 apps published somewhere under their
  licences: a separate corpus repository, say. Decide where before deleting.
- **Docs to update in the same PR:**
  - `README.md` (the `bench/corpus/` rows and the licence section);
  - `THIRD_PARTY.md` (the corpus section);
  - `docs/BENCHMARKS.md` (its `bench/corpus` links);
  - `bindings/python/README.md` (`bench/bench.py` reads corpus statements, so it needs a corpus path).
