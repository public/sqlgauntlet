#!/usr/bin/env python3
"""identity.py <run-dir> <arm>=<reference-arm> ...: does an arm give exactly what a reference harness gives (an exe
arm: another program printing the same lines) over the same engine? For each pair, every statement's C line (status,
rows, changes, ordered hash, multiset hash and error text) must be the same in both arms' <arm>.check.tsv; prints the
counts and any statement that differs. Exit 1 when one does. alt/runs is compared too but only counted: an adaptive
engine may choose its path on timings, so two runs of one build can take it a different number of times. Statements
tagged wallclock (they read the clock) are held to their row count, changes and status only, as the corpus says."""

import glob
import os
import sys

d = sys.argv[1]
bad = 0


def load(arm):
    return {p[1]: p[2:] for p in (l.rstrip('\n').split('\t') for l in open(os.path.join(d, f'{arm}.check.tsv'), errors='replace')) if p[0] == 'C'}


wall = {p[1] for f in glob.glob(os.path.join(d, 'q', '*.q')) for p in (l.split() for l in open(f) if l.startswith('@@Q ')) if 'wallclock' in p[4].split(',')}
for pair in sys.argv[2:]:
    a, r = pair.split('=')
    A, R = load(a), load(r)
    fields = ('status', 'rows', 'changes', 'hash', 'msethash', 'alt', 'error')
    diff = {i: [f for f, x, y in zip(fields, A.get(i, ['-'] * 7), R[i]) if x != y] for i in R}
    sv = sum(1 for v in diff.values() if 'alt' in v)
    diff = {i: [f for f in v if f != 'alt' and not (i in wall and f in ('hash', 'msethash'))] for i, v in diff.items()}
    diff = {i: v for i, v in diff.items() if v}
    only = set(A) - set(R)
    print(
        f'{a} vs {r}: {len(R)} statements, {len(R) - len(diff)} identical C lines ({len(wall & set(R))} wallclock by count), {len(diff)} differ'
        + (f', {len(only)} only in {a}' if only else '')
        + f'; alt/runs differs on {sv}'
    )
    for i, v in sorted(diff.items()):
        print(f'  {i}: {", ".join(v)}: {a} {A.get(i)} / {r} {R[i]}')
    bad += len(diff) + len(only)
sys.exit(1 if bad else 0)
