#!/usr/bin/env python3
"""fuzz-reduce.py <failure.json> [out.json]: a differential fuzz failure (build/fuzz/failures/, fuzz/props.py) made as
small as it goes while the backend still differs from SQLite: data rows, then schema statements (indexes first), then
the WHERE's AND-ed terms are dropped one at a time (greedy, to a fixed point, as SQLancer's reducer does). Writes a
case in tests/regressions/'s format (default: build/fuzz/reduced/<name>.json) with "expect" left for a person to
fill in once the difference is understood: same (a bug that got fixed) or "differs: <why>"."""

import json
import re
import sys
from pathlib import Path

R = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(R / 'python'))
from sqlgauntlet.abi import Backend  # noqa: E402
from sqlgauntlet.fuzz.norm import rows_key  # noqa: E402

src = Path(sys.argv[1])
case = json.loads(src.read_text())
B = R / 'build' / 'backends'
ref, other = Backend(B / 'sqlite.so'), Backend(B / f"{case['backend']}.so")


def run(b, schema, data, sql, params):
    with b.open(':memory:') as c:
        for s in schema:
            try:
                c.exec(s)
            except Exception as e:
                return None, f'schema: {e}'
        for s in data:
            try:
                c.exec(s)
            except Exception:
                pass
        ps = [c.query(f'SELECT {p}')[0][0][0][0] for p in params]
        return c.query(sql, ps)[0]


def differs(schema, data, sql):
    a = run(ref, schema, data, sql, case['params'])
    b = run(other, schema, data, sql, case['params'])
    if a[1] and a[1].startswith('schema:'):
        return False
    if (a[1] is None) != (b[1] is None):
        return True
    o = ' ORDER BY ' in sql
    return a[1] is None and rows_key(a[0], o) != rows_key(b[0], o)


schema, data, sql = list(case['schema']), list(case['data']), case['sql']
if not differs(schema, data, sql):
    sys.exit(f'fuzz-reduce: {src.name} no longer differs')
changed = True
while changed:
    changed = False
    for lst in (data, schema):
        for i in range(len(lst) - 1, -1, -1):
            trial = lst[:i] + lst[i + 1 :]
            if differs(schema if lst is data else trial, trial if lst is data else data, sql):
                lst[:] = trial
                changed = True
    m = re.search(r' WHERE (.*?)( GROUP BY | ORDER BY | LIMIT |\)? SELECT|$)', sql)
    if m and ' AND ' in m.group(1):
        terms = m.group(1).split(' AND ')
        for i in range(len(terms)):
            trial = sql.replace(m.group(1), ' AND '.join(terms[:i] + terms[i + 1 :]), 1)
            if differs(schema, data, trial):
                sql, changed = trial, True
                break
out = {
    'found': f"fuzz/props.py, oracle {case['oracle']}, reduced by tools/fuzz-reduce.py from {src.name}",
    'what': '',
    'schema': schema,
    'data': data,
    'sql': sql,
    'params': case['params'],
    'expect': {case['backend']: 'differs: '},
}
dst = Path(sys.argv[2]) if len(sys.argv) > 2 else R / 'build' / 'fuzz' / 'reduced' / (src.stem + '.json')
dst.parent.mkdir(parents=True, exist_ok=True)
dst.write_text(json.dumps(out, indent=1) + '\n')
print(f'fuzz-reduce: {len(case["schema"])}+{len(case["data"])} -> {len(schema)}+{len(data)} statements; {dst}')
