#!/usr/bin/env python3
"""corpus-split.py <turbo-repo> <commit>: sqlbench's corpus/ from cursed-sqlite-turbo's bench/corpus at <commit>.
Each application's licence decides where its statements live:
  vendor   MIT, Apache-2.0, BSD-3-Clause, MPL-2.0: committed here, under corpus/projects/<app>/ with the app's LICENSE
           and turbo's NOTICE.md beside the quoted SQL, unchanged
  fetch    GPL, AGPL, EUPL, the Elastic License 2.0 and n8n's Sustainable Use License: not committed here; corpus/fetch.sh
           copies them from cursed-sqlite-turbo at the pinned commit (they are published there under their licences)
Writes corpus/licences.tsv (app, licence, class, app repo, app commit, statements), corpus/projects/<vendored apps>,
corpus/manifest.vendored.jsonl (their manifest lines), corpus/PIN (the turbo commit) and corpus/.gitignore (the fetched
apps). Any licence not listed above stops it: a new licence is a decision, not a default."""
import io, json, subprocess, sys, tarfile, collections
from pathlib import Path

VENDOR = {'MIT', 'Apache-2.0', 'BSD-3-Clause', 'MPL-2.0'}
FETCH_PREFIX = ('GPL-', 'AGPL-', 'EUPL-', 'Elastic-2.0', 'Sustainable Use License')
repo, commit = sys.argv[1], sys.argv[2]
out = Path(__file__).resolve().parents[1] / 'corpus'


def git(*a, text=True): return subprocess.run(['git', '-C', repo, *a], check=True, capture_output=True, text=text).stdout


commit = git('rev-parse', commit).strip()
base = 'bench/corpus'
apps = sorted({l.split('/')[3] for l in git('ls-tree', '-r', '--name-only', commit, f'{base}/projects').splitlines()})
man = [json.loads(l) for l in git('show', f'{commit}:{base}/manifest.jsonl').splitlines() if l.strip()]
per = collections.Counter(r['project'] for r in man)
rows, vendored = [], []
for a in apps:
    m = json.loads(git('show', f'{commit}:{base}/projects/{a}/meta.json'))
    lic = m['license'].split(' (')[0]
    cls = 'vendor' if lic in VENDOR else 'fetch' if lic.startswith(FETCH_PREFIX) else None
    if cls is None: sys.exit(f'corpus-split: {a}: licence {m["license"]!r} is neither vendor nor fetch: decide and add it')
    rows.append((a, m['license'], cls, m['repo'], m['sha'], per[a]))
    if cls == 'vendor': vendored.append(a)
(out / 'projects').mkdir(parents=True, exist_ok=True)
tar = subprocess.run(['git', '-C', repo, 'archive', commit, *[f'{base}/projects/{a}' for a in vendored]], check=True, capture_output=True).stdout
with tarfile.open(fileobj=io.BytesIO(tar)) as t:
    for mbr in t.getmembers():
        if not mbr.isfile(): continue
        rel = Path(mbr.name).relative_to(f'{base}/projects')
        if rel.name in ('expected.json', 'timings.json'): continue   # (turbo's recorded stock results: sqlbench records its own)
        dst = out / 'projects' / rel; dst.parent.mkdir(parents=True, exist_ok=True); dst.write_bytes(t.extractfile(mbr).read())
(out / 'manifest.vendored.jsonl').write_text(''.join(json.dumps(r) + '\n' for r in man if r['project'] in vendored))
(out / 'licences.tsv').write_text('# app\tlicence\tclass\trepo\tcommit\tstatements\n' + ''.join('\t'.join(map(str, r)) + '\n' for r in rows))
(out / 'PIN').write_text(f'{commit}\n')
(out / '.gitignore').write_text('# fetched by corpus/fetch.sh (licence class "fetch" in licences.tsv)\nmanifest.fetched.jsonl\n'
                                + ''.join(f'projects/{r[0]}/\n' for r in rows if r[2] == 'fetch'))
nv = sum(r[5] for r in rows if r[2] == 'vendor'); nf = sum(r[5] for r in rows if r[2] == 'fetch')
print(f'corpus-split: {commit[:10]}: {len(vendored)} apps vendored ({nv} statements), {len(rows) - len(vendored)} fetched ({nf})')
