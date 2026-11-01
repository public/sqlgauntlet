#!/usr/bin/env python3
"""check-corpus.py [files...]: no file under corpus/projects/<app>/ for an app whose licence class is "fetch"
(corpus/licences.tsv), and every vendored app has its LICENSE and NOTICE.md. The pre-commit hook for the licence split."""

import sys
from pathlib import Path

C = Path(__file__).resolve().parents[1] / 'corpus'
cls = {p[0]: p[2] for p in (l.rstrip('\n').split('\t') for l in open(C / 'licences.tsv')) if p and not p[0].startswith('#')}
bad = [f for f in sys.argv[1:] if f.startswith('corpus/projects/') and cls.get(f.split('/')[2]) != 'vendor']
missing = [f'{a}/{n}' for a, k in cls.items() if k == 'vendor' for n in ('LICENSE', 'NOTICE.md') if not (C / 'projects' / a / n).exists()]
for f in bad:
    print(f'check-corpus: {f}: its app is not in the vendored licence class (corpus/licences.tsv)', file=sys.stderr)
for f in missing:
    print(f'check-corpus: vendored app without {f}', file=sys.stderr)
sys.exit(1 if bad or missing else 0)
