#!/usr/bin/env python3
"""workloads/tpch/make.py [SF] [out]: TPC-H at scale factor SF (default 0.1) as a corpus directory (default
build/workloads/tpch-sf<SF>): dbgen's data (fetch-dbgen.sh: pinned tpch-kit, built here) loaded by the driver, then
indexes.sql (the key and date indexes TPC-H allows, and ANALYZE). Two projects on the same data:
  tpch        the 22 queries (queries.sql, SQLite's dialect, the validation parameters; Q11's FRACTION is 0.0001 / SF),
              tagged tpch and their shape class (classes.tsv)
  tpch-small  small statements (small.sql): point lookups, short ranges and other hot cheap statements, where an
              engine's fixed cost per execution dominates; tagged small and their class
Run it with: python3 -m sqlgauntlet.run check --corpus build/workloads/tpch-sf0.1 ..."""

import subprocess
import sys
from pathlib import Path

H = Path(__file__).resolve().parent
R = H.parents[1]
sys.path.insert(0, str(H.parent))
from common import classes, named_blocks, write_project  # noqa: E402

SF = sys.argv[1] if len(sys.argv) > 1 else '0.1'
out = Path(sys.argv[2] if len(sys.argv) > 2 else R / 'build' / 'workloads' / f'tpch-sf{SF}').resolve()
tbl = out / 'data'
TABLES = ('region', 'nation', 'part', 'supplier', 'partsupp', 'customer', 'orders', 'lineitem')
if not all((tbl / f'{t}.tbl').exists() for t in TABLES):
    subprocess.run(['sh', str(H / 'fetch-dbgen.sh')], check=True)
    g = R / 'build' / 'workloads' / 'tpch' / 'dbgen'
    tbl.mkdir(parents=True, exist_ok=True)
    subprocess.run([str(g / 'dbgen'), '-b', str(g / 'dists.dss'), '-s', SF, '-f', '-q'], cwd=tbl, check=True)
meta = {
    'name': f'TPC-H SF{SF}',
    'repo': 'https://www.tpc.org/tpch/',
    'sha': 'spec 2.18; dbgen: gregrahn/tpch-kit (fetch-dbgen.sh)',
    'license': 'TPC-H specification (queries); dbgen not redistributed',
    'analyze': False,
    'workload': 'tpch',
    'scale_factor': SF,
}
load = [(t, str(tbl / f'{t}.tbl'), 'tbl') for t in TABLES]
schema, post = (H / 'schema.sql').read_text(), (H / 'indexes.sql').read_text()
cls = classes('tpch')
q = [
    (f'tpch.{n.lower()}', sql.replace('@FRACTION@', repr(0.0001 / float(SF))), ['tpch', cls.get(n, cls.get(n.lower(), ''))])
    for n, sql in named_blocks((H / 'queries.sql').read_text())
]
write_project(out, 'tpch', meta, schema, [(i, s, [t for t in tags if t]) for i, s, tags in q], load, post)
small = [(f'tpch-small.{n.replace(":", ".")}', sql, ['small', n.split(':')[0]]) for n, sql in named_blocks((H / 'small.sql').read_text())]
write_project(out, 'tpch-small', dict(meta, name=f'TPC-H SF{SF}, small statements'), schema, small, load, post)
print(f'tpch: {len(q)} queries and {len(small)} small statements at SF{SF} in {out}', file=sys.stderr)
