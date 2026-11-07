#!/usr/bin/env python3
"""smoke.py: a few vendored apps through SQLite twice and libSQL (make test). Fails when SQLite disagrees with itself or
with the manifest's row counts, when libSQL is missing a statement, or when the speed path doesn't produce timings.
libSQL may disagree with SQLite (that's what compare is for): those statements are printed, not failed."""

import os
import subprocess
import sys
from pathlib import Path

R = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(R / 'python'))
from sqlgauntlet import compare, lines  # noqa: E402

APPS = os.environ.get('TEST_APPS', 'memos atuin beets').split()
O = R / 'build' / 'test'
env = dict(os.environ, PYTHONPATH=str(R / 'python'))
arms = ['--arm', 'sqlite', '--arm', 'sqlite2=sqlite', '--arm', 'libsql']
run = [sys.executable, '-m', 'sqlgauntlet.run', '--corpus', str(R / 'corpus'), '--out', str(O), '--no-report']
subprocess.run(run[:3] + ['check', *APPS] + run[3:] + arms, check=True, env=env)
S, S2, L = (compare.load(str(O), a) for a in ('sqlite', 'sqlite2', 'libsql'))
ordered, tags, _, _ = compare.load_q(str(O))
exp = {}
for f in (O / 'q').glob('*.expected.tsv'):
    for line in open(f):
        i, r, c = line.rstrip('\n').split('\t')
        exp[i] = (r, c)
fail = []
for i, s in S.items():
    if s['st'] != 'ok':
        fail.append(f'sqlite error: {i}: {s["err"]}')
    if compare.verdict(s, S2.get(i), ordered[i], 'wallclock' in tags[i]) != 'same':
        fail.append(f'sqlite disagrees with itself: {i}')
    if i in exp and 'wallclock' not in tags[i] and (s['rows'] != exp[i][0] or ('read' not in tags[i] and s['ch'] != exp[i][1])):
        fail.append(f'sqlite vs manifest: {i}: {s["rows"]} rows, manifest {exp[i][0]}')
    if i not in L:
        fail.append(f'libsql missing {i}')
    elif compare.verdict(s, L[i], ordered[i], 'wallclock' in tags[i]) != 'same':
        print(f'smoke: libsql differs on {i} ({compare.verdict(s, L[i], ordered[i], "wallclock" in tags[i])})')
subprocess.run(run[:3] + ['speed', APPS[0], '--reps', '5', '--budget', '2'] + run[3:] + arms[:2], check=True, env=env)
T = [line for line in open(O / 'sqlite.hot.1.tsv') if lines.split(line)[0] == 'timing']
if not T:
    fail.append('speed: no timing lines')
print(f'smoke: {len(S)} statements, {len(exp)} with manifest counts, {len(fail)} failures')
for f in fail:
    print('  ' + f)
sys.exit(1 if fail else 0)
