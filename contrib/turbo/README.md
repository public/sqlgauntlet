# contrib/turbo

An example of testing an engine that lives in its own repository:
[cursed-sqlite-turbo](https://github.com/public/cursed-sqlite-turbo), a specialising layer over SQLite. Run
`sh contrib/turbo/build.sh <turbo-checkout>` to build it as `build/backends/turbo.so`. The script uses only the
generic `make sqlite-api` target. The turbo backend runs with `QC_SERVE=1` set by default, and reports its
compiled-plan executions as the alternative path.

```bash
cd python && python3 -m sqlgauntlet.run check --arm sqlite --arm turbo
```

`sh contrib/turbo/build.sh <turbo-checkout> <its bench/corpus/corpus.c>` also builds turbo's own bench driver over
both engines, so that `tools/identity.py` can hold sqlgauntlet's results to it. On the 1,302-statement corpus, every
line was identical.

This directory is only an example. The build belongs in turbo's own tree, with sqlgauntlet as a pinned dependency.
