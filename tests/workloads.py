#!/usr/bin/env python3
"""workloads.py: the workload path without its downloads (make test). TPC-H's make.py on five generated rows per
table, in its schema's shape, instead of dbgen's: the driver's load, post.sql, then every one of the 22 queries and
the small statements must run without an error on SQLite, and agree between two arms of it."""

import os
import re
import subprocess
import sys
from pathlib import Path

R = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(R / 'python'))
from sqlgauntlet import compare  # noqa: E402

O = R / 'build' / 'test-workloads'
data = O / 'corpus' / 'data'
data.mkdir(parents=True, exist_ok=True)
schema = (R / 'workloads' / 'tpch' / 'schema.sql').read_text()
for m in re.finditer(r'CREATE TABLE (\w+)\s*\((.*?)\);', schema, re.S):
    cols = [c.strip().split()[0] for c in re.split(r',(?![^()]*\))', m.group(2)) if c.strip() and not c.strip().upper().startswith('PRIMARY')]
    rows = []
    for k in range(1, 6):
        v = []
        for c in cols:
            if 'date' in c:
                v.append(f'1995-0{k}-1{k}')
            elif c.endswith(('key', 'quantity', 'size', 'priority')) and 'clerk' not in c:
                v.append(str(k))
            elif c.endswith(('price', 'cost', 'bal', 'discount', 'tax')):
                v.append(f'{k}.5')
            else:
                v.append(f'{c}-{k}')
        rows.append('|'.join(v) + '|')
    (data / f'{m.group(1)}.tbl').write_text('\n'.join(rows) + '\n')
subprocess.run([sys.executable, str(R / 'workloads' / 'tpch' / 'make.py'), '0.01', str(O / 'corpus')], check=True)
env = dict(os.environ, PYTHONPATH=str(R / 'python'))
subprocess.run(
    [
        sys.executable,
        '-m',
        'sqlgauntlet.run',
        'check',
        '--corpus',
        str(O / 'corpus'),
        '--out',
        str(O / 'run'),
        '--arm',
        'sqlite',
        '--arm',
        'sqlite2=sqlite',
        '--no-report',
    ],
    check=True,
    env=env,
)
S, S2 = compare.load(str(O / 'run'), 'sqlite'), compare.load(str(O / 'run'), 'sqlite2')
ordered, tags, _, _ = compare.load_q(str(O / 'run'))
fail = [f'{i}: {x["st"]} {x["err"]}' for i, x in S.items() if x['st'] != 'ok']
fail += [f'{i}: sqlite2 {compare.verdict(x, S2.get(i), ordered[i], False)}' for i, x in S.items() if compare.verdict(x, S2.get(i), ordered[i], False) != 'same']
if len(S) != 40:
    fail.append(f'{len(S)} statements, not 40')
print(f'workloads: {len(S)} TPC-H statements, {len(fail)} failures')
for f in fail:
    print('  ' + f)
sys.exit(1 if fail else 0)
