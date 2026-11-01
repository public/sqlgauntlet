#!/usr/bin/env python3
"""summarise.py <dir> <arm>...: speedtest1 runs (<arm>.<round>.txt) as a Markdown table: median seconds per test and
arm, each arm relative to the first (the reference), and the verification hashes (must be one value)."""

import glob
import os
import re
import statistics
import sys

d = sys.argv[1]
runs, hashes, order, names = {}, {}, [], {}
for f in sorted(glob.glob(os.path.join(d, '*.txt'))):
    arm, rnd = os.path.basename(f)[:-4].rsplit('.', 1)
    ts = 'main'  # (mix1, the default, runs several testsets whose test numbers repeat: tests are testset/number)
    for line in open(f, errors='replace'):
        b = re.match(r'\s*Begin testset "(\w+)"', line)
        if b:
            ts = b.group(1)
        m = re.match(r'\s*(\d+) - (.*?)\.*\s+(\d+\.\d+)s\s*$', line)
        if m:
            t = f'{ts}/{m.group(1)}'
            names[t] = m.group(2).strip()
            if t not in order:
                order.append(t)
            runs.setdefault(arm, {}).setdefault(t, []).append(float(m.group(3)))
        m = re.match(r'\s*TOTAL\.*\s+(\d+\.\d+)s', line)
        if m:
            runs.setdefault(arm, {}).setdefault('TOTAL', []).append(float(m.group(1)))
        m = re.match(r'Verification Hash: (.*)', line)
        if m:
            hashes.setdefault(m.group(1).strip(), []).append(f'{arm}.{rnd}')
arms = [a for a in sys.argv[2:] if a in runs] or sorted(runs)
ref = arms[0]
med = lambda a, t: statistics.median(runs[a][t]) if a in runs and t in runs[a] else float('nan')
xs = arms[1:]
print('| test | ' + ' | '.join(arms) + ''.join(f' | {a}/{ref}' for a in xs) + ' |')
print('|---|' + '---:|' * (len(arms) + len(xs)))
for t in order + ['TOTAL']:
    s = med(ref, t)
    rel = lambda a: f'{med(a, t) / s:.3f}' if a in runs and s > 0 else '–'  # (nan > 0 is False)
    label = f'{t} {names[t]}' if t != 'TOTAL' else '**total**'
    print(f'| {label} | ' + ' | '.join(f'{med(a, t):.3f}' for a in arms) + ''.join(f' | {rel(a)}' for a in xs) + ' |')
print()
print(f'verification hashes: {len(hashes)} distinct' + ('' if len(hashes) == 1 else ' (MISMATCH)'))
for h, w in hashes.items():
    print(f'  {h}: {", ".join(w)}')
