"""common: a standard workload as a corpus directory (corpus/README.md's layout), so the runner, compare and summarise
treat it like the application corpus. A workload's make.py fetches its pinned inputs, generates its data, and calls
write_project. Data too big for data.sql is loaded by the driver's load mode from load.tsv (table, file, tbl|csv),
then post.sql runs (indexes, ANALYZE)."""

import json
import re
from pathlib import Path

CLASSES = Path(__file__).resolve().parent / 'classes.tsv'


def named_blocks(text):
    """[(name, sql)] from a file of queries separated by '-- name: <name>' lines (anything before the first is a header)"""
    out, cur = [], None
    for line in text.splitlines():
        m = re.match(r'\s*--\s*name:\s*(\S+)', line)
        if m:
            cur = [m.group(1), []]
            out.append(cur)
        elif cur is not None and not re.match(r'\s*--', line):
            cur[1].append(line)
    return [(n, '\n'.join(s).strip().rstrip(';') + ';') for n, s in out if '\n'.join(s).strip()]


def classes(source):
    """{item: shape class} from classes.tsv for one source (tpch, speedtest1)"""
    c = {}
    for line in open(CLASSES):
        p = line.rstrip('\n').split('\t')
        if len(p) == 3 and not p[0].startswith('#') and p[0] == source:
            c[p[1]] = p[2]
    return c


def auto_tags(sql):
    """the tags the harness acts on, from the text: read, orderby / topn when the outermost query orders"""
    s = re.sub(r"'(?:[^']|'')*'", "''", sql)
    while True:  # (drop parenthesised parts: an ORDER BY inside a subquery doesn't order the result)
        t = re.sub(r'\([^()]*\)', '', s)
        if t == s:
            break
        s = t
    tags = []
    if re.match(r'\s*(SELECT|WITH|VALUES)\b', sql, re.I):
        tags.append('read')
    if re.search(r'\bORDER\s+BY\b', s, re.I):
        tags.append('topn' if re.search(r'\bLIMIT\b', s, re.I) else 'orderby')
    return tags


def write_project(out, app, meta, schema, queries, load=(), post='', data=''):
    """out/projects/<app>/ and its lines in out/manifest.jsonl. queries: [(id, sql, tags)]; load: [(table, file, fmt)]"""
    d = Path(out) / 'projects' / app
    d.mkdir(parents=True, exist_ok=True)
    (d / 'meta.json').write_text(json.dumps(meta, indent=1) + '\n')
    (d / 'schema.sql').write_text(schema)
    (d / 'data.sql').write_text(data)
    if load:
        (d / 'load.tsv').write_text(''.join(f'{t}\t{f}\t{fmt}\n' for t, f, fmt in load))
    if post:
        (d / 'post.sql').write_text(post)
    blocks, man = [], []
    for i, sql, tags in queries:
        tags = sorted(set(tags) | set(auto_tags(sql)))
        blocks.append(f'-- @id {i}\n-- @tags {",".join(tags)}\n{sql}\n')
        src = {k: meta.get(k, '') for k in ('repo', 'sha', 'license')}
        man.append({'project': app, 'id': i, **src, 'tags': tags, 'sql': sql})
    (d / 'queries.sql').write_text('\n'.join(blocks))
    m = Path(out) / 'manifest.jsonl'
    keep = [line for line in (m.read_text().splitlines() if m.exists() else []) if json.loads(line)['project'] != app]
    m.write_text(''.join(line + '\n' for line in keep) + ''.join(json.dumps(r) + '\n' for r in man))
    return d
