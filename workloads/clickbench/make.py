#!/usr/bin/env python3
"""workloads/clickbench/make.py [rows] [out]: ClickBench's 43 queries (its sqlite/ entry at a pinned commit) on the
first <rows> records of its hits dataset (default 1,000,000; fetch.sh downloads only that prefix, checksummed), as a
corpus directory (default build/workloads/clickbench-<rows>). The table is loaded as ClickBench loads SQLite: its
create.sql, then every CSV field imported as text, so the columns' declared types convert them; no indexes, no
ANALYZE. Each query is tagged clickbench."""

import os
import subprocess
import sys
from pathlib import Path

H = Path(__file__).resolve().parent
R = H.parents[1]
sys.path.insert(0, str(H.parent))
from common import write_project  # noqa: E402

ROWS = sys.argv[1] if len(sys.argv) > 1 else '1000000'
out = Path(sys.argv[2] if len(sys.argv) > 2 else R / 'build' / 'workloads' / f'clickbench-{ROWS}').resolve()
subprocess.run(['sh', str(H / 'fetch.sh'), ROWS], check=True)
D = Path(os.environ.get('SQLGAUNTLET_DATA', R / 'build' / 'data')) / 'clickbench'
qs = [line.strip() for line in (D / 'queries.sql').read_text().splitlines() if line.strip()]
meta = {
    'name': f'ClickBench, first {ROWS} rows',
    'repo': 'https://github.com/ClickHouse/ClickBench',
    'sha': 'db61f6ad831cb6c8b8a65724e65d8c9764b5b00b',
    'license': 'Apache-2.0 (queries); the dataset is fetched, not redistributed',
    'analyze': False,
    'workload': 'clickbench',
    'rows': ROWS,
}
write_project(
    out,
    'clickbench',
    meta,
    (D / 'create.sql').read_text(),
    [(f'clickbench.q{k:02d}', q if q.endswith(';') else q + ';', ['clickbench']) for k, q in enumerate(qs)],
    [('hits', str(D / f'hits-{ROWS}.csv'), 'csv')],
)
print(f'clickbench: {len(qs)} queries on {ROWS} rows in {out}', file=sys.stderr)
