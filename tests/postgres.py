#!/usr/bin/env python3
"""postgres.py: a few vendored apps through SQLite and PostgreSQL (make test-postgres; SG_PG_URL names the server, and
sqlglot must be importable). It fails when the harness fails: an app's database not exported or built, a statement
without a result line. Disagreements are printed, not failed: they are translation gaps or real differences, which
is what compare reports (sqlglot leaves many of SQLite's idioms untranslated: INSERT OR IGNORE, GLOB, strftime, JSON
on text columns). It also fails if an app has no statement that agrees: its export or load broke, not a statement."""

import collections
import os
import subprocess
import sys
from pathlib import Path

R = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(R / 'python'))
from sqlgauntlet import compare  # noqa: E402

APPS = os.environ.get('TEST_APPS', 'memos atuin beets').split()
O = R / 'build' / 'test-postgres'
env = dict(os.environ, PYTHONPATH=str(R / 'python'))
args = ['check', *APPS, '--corpus', str(R / 'corpus'), '--out', str(O), '--arm', 'sqlite', '--arm', 'postgres', '--no-report']
subprocess.run([sys.executable, '-m', 'sqlgauntlet.run', *args], check=True, env=env)
S, PG = compare.load(str(O), 'sqlite'), compare.load(str(O), 'postgres')
ordered, tags, _, untr = compare.load_q(str(O))
v = {
    i: 'untranslated' if i in untr and i not in PG else compare.verdict(x, PG.get(i), ordered[i], 'wallclock' in tags[i], 'read' in tags[i])
    for i, x in S.items()
}
c = collections.Counter(v.values())
fail = [f'{i}: no result line' for i, x in v.items() if x == 'missing']
for app in APPS:
    if not any(x == 'same' for i, x in v.items() if i.startswith(app + '.')):
        fail.append(f'{app}: no statement agrees (its export or load?)')
print(f'postgres: {len(S)} statements: ' + ', '.join(f'{k} {n}' for k, n in c.most_common()) + f'; {len(fail)} failures')
for f in fail:
    print('  ' + f)
sys.exit(1 if fail else 0)
