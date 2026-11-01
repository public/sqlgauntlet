"""prep: a corpus directory (projects/<p>/{meta.json,schema.sql,data.sql,queries.sql} and manifest.jsonl, the layout of
cursed-sqlite-turbo's bench/corpus) as the driver's flat files, one pair per project:
  <p>.build.sql   the database: journal_mode OFF, temp _cfg(scale), schema.sql, data.sql, ANALYZE unless meta.json says
                  "analyze": false
  <p>.q           meta.json's "pragmas" (@@X), then each statement: @@Q <id> <ordered> <write> <tags>, its @@P
                  parameters, its SQL
ordered = 1 when the statement has ORDER BY (rows compared in order, else as a multiset); write = 1 when it is not a
read (it runs inside BEGIN ... ROLLBACK). This is turbo's bench/corpus/prep.py's output, byte for byte, for dialect sqlite.

Other dialects (dialect.py): each statement's text for that dialect comes from the corpus's overrides
(<corpus>/dialects/<dialect>.jsonl, {"id", "sql"} or {"id", "untranslatable": reason}), else from sqlglot's
transpiler. A statement with no text for the dialect goes to <p>.untranslated.tsv with the reason, never dropped
silently: compare counts it."""

import json
import os
import re
import sys
from pathlib import Path

WAVE1 = {
    'anki',
    'atuin',
    'audiobookshelf',
    'beets',
    'calibre',
    'chromium-history',
    'claude-mem',
    'darktable',
    'datasette',
    'firefox-places',
    'freshrss',
    'grafana',
    'home-assistant',
    'jellyfin',
    'joplin',
    'kodi',
    'memos',
    'n8n',
    'navidrome',
    'pihole-ftl',
    'signal-desktop',
    'synapse',
    'syncthing',
    'trilium',
    'uptime-kuma',
    'vaultwarden',
    'zotero',
}


def parse_queries(path):
    """queries.sql's annotated blocks (turbo's tools/run.py parser: blocks start with -- @id)"""
    res, cur = [], None
    for line in Path(path).read_text().splitlines():
        m = re.match(r"\s*--\s*@(\w+)\s?(.*)$", line)
        if m:
            k, v = m.group(1), m.group(2).strip()
            if k == 'id':
                cur = {'id': v, 'sql': [], 'params': []}
                res.append(cur)
            elif cur is not None and k == 'param':
                n, _, lit = v.partition(' ')
                cur['params'].append((n, lit.strip()))
            continue
        if cur is not None:
            if re.match(r"\s*--", line) and not cur['sql']:
                continue
            cur['sql'].append(line)
    for q in res:
        q['sql'] = '\n'.join(q['sql']).strip()
        if not q['sql'].endswith(';'):
            q['sql'] += ';'
    return res


def _put(path, text):  # (unchanged files keep their time: a database is rebuilt only when its build.sql is newer)
    if not path.exists() or path.read_text() != text:
        path.write_text(text)


def manifest(corpus):
    """every manifest*.jsonl: turbo's one manifest.jsonl, or sqlgauntlet's vendored and fetched halves"""
    man = {}
    for f in sorted(Path(corpus).glob('manifest*.jsonl')):
        for l in open(f):
            if l.strip():
                r = json.loads(l)
                man[r['id']] = r
    return man


def prep(corpus, out, scale='1', projects=(), ids=None, wave='', heavy=False, dialect='sqlite', log=sys.stderr):
    """write the flat files; returns the number of statements written"""
    corpus, out = Path(corpus), Path(out)
    out.mkdir(parents=True, exist_ok=True)
    man = manifest(corpus)
    want = set(projects)
    tr = None
    if dialect != 'sqlite':
        from . import dialect as D

        tr = D.Translator(corpus, dialect)
    n = 0
    for pdir in sorted((corpus / 'projects').iterdir()):
        p = pdir.name
        if not (pdir / 'queries.sql').exists() or (want and p not in want):
            continue
        if (wave == '1' and p not in WAVE1) or (wave == '2' and p in WAVE1):
            continue
        qs = [
            q for q in parse_queries(pdir / 'queries.sql') if (ids is None or q['id'] in ids) and (heavy or 'heavy' not in man.get(q['id'], {}).get('tags', []))
        ]
        if not qs:
            continue
        meta = json.loads((pdir / 'meta.json').read_text())
        build = (
            f"PRAGMA journal_mode=OFF;\nCREATE TEMP TABLE _cfg AS SELECT {scale} AS scale;\n"
            + (pdir / 'schema.sql').read_text()
            + '\n'
            + (pdir / 'data.sql').read_text()
            + ('\nANALYZE;\n' if meta.get('analyze', True) else '\n')
        )
        f = [f"@@X {x.rstrip(';')};\n" for x in meta.get('pragmas', [])]
        skipped = []
        for q in qs:
            tags = man.get(q['id'], {}).get('tags', [])
            sql = q['sql']
            if tr:
                sql, why = tr.statement(q['id'], sql)
                if sql is None:
                    skipped.append((q['id'], why))
                    continue
            ordered = int('orderby' in tags or 'topn' in tags)
            write = int('read' not in tags)
            f.append(f"@@Q {q['id']} {ordered} {write} {','.join(tags) or '-'}\n")
            for nm, lit in q['params']:
                f.append(f'@@P {nm} {lit}\n')
            f.append(sql + '\n')
            n += 1
        if tr:
            build = tr.script(p, build)
        _put(out / f'{p}.build.sql', build)
        _put(out / f'{p}.q', ''.join(f))
        if str(scale) == '1':  # (the manifest's row and change counts were recorded at scale 1: compare checks every arm against them)
            _put(
                out / f'{p}.expected.tsv',
                ''.join(f"{q['id']}\t{man[q['id']]['rows']}\t{man[q['id']]['changes']}\n" for q in qs if q['id'] in man and 'rows' in man[q['id']]),
            )
        if tr:
            _put(out / f'{p}.untranslated.tsv', ''.join(f'{i}\t{w}\n' for i, w in skipped))
    if ids is not None and n < len(ids):
        print(f'prep: {len(ids) - n} of the ids not in the corpus', file=log)
    print(f'prep: {n} statements in {out}', file=log)
    return n


def main(argv=None):
    import argparse

    a = argparse.ArgumentParser(prog='sqlgauntlet-prep', description=__doc__.split('\n')[0])
    a.add_argument('corpus')
    a.add_argument('out')
    a.add_argument('projects', nargs='*')
    a.add_argument('--scale', default='1')
    a.add_argument('--ids', help='a file whose lines start with statement ids')
    a.add_argument('--wave', default=os.environ.get('WAVE', ''))
    a.add_argument('--heavy', action='store_true')
    a.add_argument('--dialect', default='sqlite')
    o = a.parse_args(argv)
    ids = {l.split('\t')[0].split()[0] for l in open(o.ids) if l.strip() and not l.startswith('#')} if o.ids else None
    prep(o.corpus, o.out, o.scale, o.projects, ids, o.wave, o.heavy, o.dialect)


if __name__ == '__main__':
    main()
