#!/usr/bin/env python3
"""identity.py <run-dir> <arm>=<reference-arm> ...: is sqlbench's driver measuring what cursed-sqlite-turbo's own bench
measures? For each pair, every statement's C line (status, rows, changes, ordered hash, multiset hash and error
text) must be the same in both arms' <arm>.check.tsv; prints the counts and any statement that differs. Exit 1 when
one does. served/runs is compared too but only counted: turbo's adapt decides on timings, so two runs of one build can
serve a statement a different number of times."""
import sys, os

d = sys.argv[1]; bad = 0
def load(arm):
    return {p[1]: p[2:] for p in (l.rstrip('\n').split('\t') for l in open(os.path.join(d, f'{arm}.check.tsv'), errors='replace')) if p[0] == 'C'}
for pair in sys.argv[2:]:
    a, r = pair.split('='); A, R = load(a), load(r)
    fields = ('status', 'rows', 'changes', 'hash', 'msethash', 'served', 'error')
    diff = {i: [f for f, x, y in zip(fields, A.get(i, ['-'] * 7), R[i]) if x != y] for i in R}
    sv = sum(1 for v in diff.values() if 'served' in v)
    diff = {i: [f for f in v if f != 'served'] for i, v in diff.items()}
    diff = {i: v for i, v in diff.items() if v}
    only = set(A) - set(R)
    print(f'{a} vs {r}: {len(R)} statements, {len(R) - len(diff)} identical C lines, {len(diff)} differ'
          + (f', {len(only)} only in {a}' if only else '') + f'; served/runs differs on {sv}')
    for i, v in sorted(diff.items()):
        print(f'  {i}: {", ".join(v)}: {a} {A.get(i)} / {r} {R[i]}')
    bad += len(diff) + len(only)
sys.exit(1 if bad else 0)
