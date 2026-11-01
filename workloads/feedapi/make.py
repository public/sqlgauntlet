#!/usr/bin/env python3
"""workloads/feedapi/make.py [--out DIR]: the twelve-dollar server challenge's feed API as a corpus directory (default
build/workloads/feedapi), with the SQL of the entry that serves it from the sqlite3 CLI (sql/, NOTICE.md): the
challenge's schema and seed data (50,000 users, 500,000 posts, ~2,000,000 likes), and the application's own SQL as
statements. Needs node (the challenge's seed generator) and the sqlite3 CLI (gen-hmac.sql is SQL that writes SQL).
The connection setup (connect.sql) is the application's: its temp tables, the HTTP parser trigger, the views that
render posts and the feed as JSON, and HMAC-SHA256 as a recursive CTE, with one authenticated request already parsed.
The statements:
  feedapi.http.parse       a request (a like, with its bearer token) into temp.req: the trigger's seven-stage parse
  feedapi.jwt.hmac         the token's HMAC-SHA256: 64 rounds a block, as a recursive CTE with no XOR and no functions
  feedapi.jwt.auth         the route's whole token check (signature, header, payload, expiry) into temp.jv
  feedapi.route.feed       the feed: the newest 20 posts as one JSON document
  feedapi.route.post       one post as JSON, with its author and like count
  feedapi.route.like       a like (ON CONFLICT DO NOTHING); feedapi.route.create: a new post
  feedapi.http.response    the full HTTP response text, Content-Length from octet_length()"""

import argparse
import json
import subprocess
import sys
from pathlib import Path

H = Path(__file__).resolve().parent
R = H.parents[1]
sys.path.insert(0, str(H.parent))
from common import write_project  # noqa: E402

a = argparse.ArgumentParser()
a.add_argument('--out', default=str(R / 'build' / 'workloads' / 'feedapi'))
a.add_argument('--seed-db', help="the challenge's seed/feed.db, built already (its tokens.json beside it): read instead of running the generator")
o = a.parse_args()
B = R / 'build' / 'workloads' / 'feedapi'
subprocess.run(['sh', str(H / 'fetch.sh')], check=True)
tsv = B / 'tsv'
if o.seed_db:  # (the same rows generate.mjs writes, read back from a database make-seed.sh built)
    import shutil
    import sqlite3

    tsv.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(f'file:{o.seed_db}?mode=ro', uri=True)
    for t in ('users', 'posts', 'likes'):
        with open(tsv / f'{t}.tsv', 'w') as f:
            for r in con.execute(f'SELECT * FROM {t} ORDER BY rowid'):
                f.write('\t'.join(str(v) for v in r) + '\n')
    con.close()
    shutil.copyfile(Path(o.seed_db).parent / 'tokens.json', tsv / 'tokens.json')
elif not (tsv / 'likes.tsv').exists():
    subprocess.run(['node', str(B / 'src' / 'seed' / 'generate.mjs'), str(tsv)], check=True)
hmac = subprocess.run(['sqlite3', ':memory:'], input=(H / 'sql' / 'gen-hmac.sql').read_text(), capture_output=True, text=True, check=True).stdout
token = json.loads((tsv / 'tokens.json').read_text())[0]['token']
head = f'POST /posts/42/like HTTP/1.1\r\nHost: localhost\r\nAuthorization: Bearer {token}\r\nContent-Length: 0\r\n'
lit = lambda t: "'" + t.replace("'", "''").replace('\r\n', "' || char(13, 10) || '") + "'"  # (one line: the corpus format's)
app = '\n'.join(line for line in (H / 'sql' / 'app.sql').read_text().splitlines() if not line.startswith('.read '))
connect = (
    'PRAGMA temp_store = MEMORY;\n'  # (before any temp table: changing it later drops them)
    "CREATE TEMP TABLE cfg AS SELECT 'twelve-dollar-challenge' AS secret, 0 AS boot;\n"
    + app.replace('CREATE TEMP VIEW jr AS', hmac + '\nCREATE TEMP VIEW jr AS', 1)
    + f"\nINSERT INTO temp.req VALUES (1, {lit(head)}, '');\n"
)
if 'CREATE TEMP VIEW jr AS' not in app:
    sys.exit('feedapi: app.sql changed: no CREATE TEMP VIEW jr')
route = lambda f: '\n'.join(line for line in (H / 'sql' / 'route' / f).read_text().splitlines() if not line.startswith(('.read ', '--')))
q = [
    ('feedapi.http.parse', 'REPLACE INTO temp.req VALUES (1, ?1, \'\');', ['http', 'write']),
    ('feedapi.jwt.hmac', 'SELECT sig FROM temp.jsig;', ['jwt']),
    ('feedapi.jwt.auth', route('auth.sql'), ['jwt', 'wallclock']),
    ('feedapi.route.feed', 'SELECT b FROM temp.feed;', ['route', 'hotpath']),
    ('feedapi.route.post', 'SELECT j FROM temp.pj WHERE id = ?1;', ['route', 'hotpath']),
    ('feedapi.route.like', 'INSERT INTO likes (user_id, post_id) VALUES (?1, ?2) ON CONFLICT DO NOTHING;', ['route', 'write']),
    ('feedapi.route.create', 'INSERT INTO posts (user_id, body) VALUES (?1, ?2);', ['route', 'write', 'wallclock']),
    ('feedapi.http.response', 'SELECT r FROM temp.http;', ['http']),
]
meta = {
    'name': 'Twelve-dollar server challenge: the feed API in SQL',
    'repo': 'https://github.com/arjaythedev/twelve-dollar-server-challenge',
    'sha': 'c1cdb38ae3d515ac026bfd54cd5d88c16aa76d41',
    'license': 'MIT',
    'workload': 'feedapi',
}
d = write_project(o.out, 'feedapi', meta, (B / 'src' / 'schema.sql').read_text(), q, [(t, str(tsv / f'{t}.tsv'), 'tsv') for t in ('users', 'posts', 'likes')])
(d / 'connect.sql').write_text(connect)
params = {
    'feedapi.http.parse': [('?1', lit(head))],
    'feedapi.route.post': [('?1', '4242')],
    'feedapi.route.like': [('?1', '7'), ('?2', '4242')],
    'feedapi.route.create': [('?1', '7'), ('?2', "'hello from sqlgauntlet'")],
}
blocks = (d / 'queries.sql').read_text().split('-- @id ')
for k, b in enumerate(blocks[1:], 1):
    i, _, rest = b.partition('\n')
    blocks[k] = i + '\n' + ''.join(f'-- @param {n} {v}\n' for n, v in params.get(i.strip(), [])) + rest
(d / 'queries.sql').write_text('-- @id '.join(blocks))
print(f'feedapi: {len(q)} statements in {o.out}', file=sys.stderr)
