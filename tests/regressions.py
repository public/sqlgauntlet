#!/usr/bin/env python3
"""regressions.py [backend...]: every case in tests/regressions/ (a confirmed fuzz finding, minimised: schema, data,
statement, parameters) replayed on SQLite and each backend (default: every SQLite-dialect one built), through the
backend ABI. A case's "expect" says, per backend, "same" (the default: rows equal SQLite's, normalised as the fuzz
oracles do) or "differs: <why>" (a known difference, kept so that a fix shows up: it then fails as "now same", and
the case's expect is updated). Exit 1 when any backend's result is not what the case expects. make test runs it."""

import json
import sys
from pathlib import Path

R = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(R / 'python'))
from sqlgauntlet.abi import Backend  # noqa: E402
from sqlgauntlet.fuzz.norm import rows_key  # noqa: E402

BK = R / 'build' / 'backends'
names = sys.argv[1:] or [n for n in ('turbo', 'libsql', 'turso') if (BK / f'{n}.so').exists()]
backends = {n: Backend(BK / f'{n}.so') for n in ['sqlite'] + names}


def run(b, case):
    with b.open(':memory:') as c:
        for s in case['schema'] + case['data']:
            try:
                c.exec(s)
            except Exception as e:
                return None, f'setup: {e}'
        params = [c.query(f'SELECT {p}')[0][0][0][0] for p in case.get('params', [])]
        ((rows, err),) = c.query(case['sql'], params)
        return rows, err


fail = 0
for f in sorted((R / 'tests' / 'regressions').glob('*.json')):
    case = json.loads(f.read_text())
    ordered = ' ORDER BY ' in case['sql']
    ref = run(backends['sqlite'], case)
    for n in names:
        got = run(backends[n], case)
        same = (ref[1] is None) == (got[1] is None) and (ref[1] is not None or rows_key(ref[0], ordered) == rows_key(got[0], ordered))
        want = case.get('expect', {}).get(n, 'same')
        ok = same == (want == 'same')
        print(f'{"ok  " if ok else "FAIL"} {f.stem} {n}: {"same" if same else "differs"}' + ('' if ok else f' (expected {want})'))
        fail += not ok
print(f'regressions: {len(list((R / "tests" / "regressions").glob("*.json")))} cases, {fail} unexpected')
sys.exit(1 if fail else 0)
