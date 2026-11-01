#!/usr/bin/env python3
"""summarise.py <runs-dir>: workloads/sqlancer/run.sh outputs as a Markdown table, one row per backend x oracle x
seed: queries SQLancer ran and the bugs it logged (logs/sqlite3/*.log other than the *-cur.log of the database in
progress), then each bug log's failing check, native crashes and exceptions."""

import glob
import os
import re
import sys

d = sys.argv[1]
print('| backend | oracle | seed | queries | bug logs |')
print('|---|---|---|---:|---:|')
details = []
for w in sorted(glob.glob(os.path.join(d, '*-*-*'))):
    arm, oracle, seed = os.path.basename(w).split('-', 2)
    txt = open(os.path.join(w, 'sqlancer.txt'), errors='replace').read() if os.path.exists(os.path.join(w, 'sqlancer.txt')) else ''
    q = re.findall(r'^\s+([\d,.]+k?) queries$', txt, re.M)
    q = q[-1] if q else ('crashed' if 'fatal error' in txt else '?')
    bugs = [b for b in glob.glob(os.path.join(w, 'logs', 'sqlite3', '*.log')) if not b.endswith('-cur.log')]
    if 'fatal error' in txt:
        fr = re.search(r'Problematic frame:\n# (.*)', txt)
        details.append(f'{arm}/{oracle}/{seed}: native crash: {fr.group(1) if fr else "?"}')
    if 'Exception' in txt and not bugs:
        details.append(f'{arm}/{oracle}/{seed}: exception in sqlancer.txt: ' + next(l for l in txt.splitlines() if 'Exception' in l)[:300])
    for b in bugs:
        lines = [l for l in open(b, errors='replace').read().splitlines() if l.strip()]
        head = next((l for l in lines if l.startswith('--') and ('rror' in l or 'xception' in l or 'ssertion' in l)), lines[0] if lines else '')
        details.append(f'{arm}/{oracle}/{seed}: {os.path.basename(b)}: {head[:300]}')
    print(f'| {arm} | {oracle} | {seed} | {q} | {len(bugs)} |')
print()
for x in details:
    print('- ' + x)
