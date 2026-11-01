"""mutate: the corpus's read statements, mutated, as a new corpus directory to run differentially (sqlgauntlet-run
check --corpus <out> --arm sqlite --arm <backend>). Each statement keeps its app's schema and data, so the mutants
stay meaningful; a mutant every engine refuses shows as both-error, which is fine. Needs sqlglot (the statement is
parsed, changed in its syntax tree and printed back as SQLite SQL; parameters survive as markers).
  python -m sqlgauntlet.fuzz.mutate <corpus> <out> [--per N] [--seed S] [apps...]
Mutations, one or two per mutant: a number becomes n+1, n-1, 0, -1 or 2^31; a string becomes '', '%' or its upper
case; LIMIT n becomes 0, 1 or n+3, maybe with OFFSET 1; the WHERE becomes NOT (w), (w) OR 1 = 0 or (w) AND 1 = 1 (the
last two must not change the rows: a metamorphic check across the run's arms too); the outer SELECT gains DISTINCT.
Mutant ids are <id>~m<k>, with the original's tags and parameters."""

import argparse
import json
import random
import re
import shutil
from pathlib import Path

from ..dialect import number_params
from ..prep import manifest, parse_queries


def mutants(sg, sql, rng, n):
    marked, names = number_params(sql)
    back = {v: k for k, v in names.items()}
    try:
        tree = sg.parse_one(marked.rstrip(';'), read='sqlite')
    except Exception:
        return []
    if not isinstance(tree, (sg.exp.Select, sg.exp.Union, sg.exp.Intersect, sg.exp.Except)):
        return []
    out = set()
    for _ in range(n * 4):
        if len(out) >= n:
            break
        t = tree.copy()
        for _ in range(rng.choice((1, 1, 2))):
            kind = rng.choice(('num', 'str', 'limit', 'where', 'distinct'))
            if kind == 'num':
                xs = [x for x in t.find_all(sg.exp.Literal) if not x.is_string and re.fullmatch(r'-?\d+', x.this)]
                if xs:
                    x = rng.choice(xs)
                    v = int(x.this)
                    x.set('this', str(rng.choice((v + 1, v - 1, 0, -1, 2**31))))
            elif kind == 'str':
                xs = [x for x in t.find_all(sg.exp.Literal) if x.is_string]
                if xs:
                    x = rng.choice(xs)
                    x.set('this', rng.choice(('', '%', x.this.upper())))
            elif kind == 'limit':
                lim = t.args.get('limit')
                if lim is not None and isinstance(lim.expression, sg.exp.Literal):
                    v = int(lim.expression.this) if lim.expression.this.isdigit() else 1
                    lim.set('expression', sg.exp.Literal.number(rng.choice((0, 1, v + 3))))
                    if rng.random() < 0.3:
                        t.set('offset', sg.exp.Offset(expression=sg.exp.Literal.number(1)))
            elif kind == 'where':
                w = t.args.get('where')
                if w is not None:
                    c = w.this
                    new = rng.choice(
                        (
                            sg.exp.Not(this=sg.exp.Paren(this=c.copy())),
                            sg.exp.Or(this=sg.exp.Paren(this=c.copy()), expression=sg.parse_one('1 = 0')),
                            sg.exp.And(this=sg.exp.Paren(this=c.copy()), expression=sg.parse_one('1 = 1')),
                        )
                    )
                    w.set('this', new)
            elif kind == 'distinct' and isinstance(t, sg.exp.Select) and not t.args.get('distinct'):
                t.set('distinct', sg.exp.Distinct())
        try:
            s = t.sql(dialect='sqlite')
        except Exception:
            continue
        s = re.sub(r'__sgp(\d+)__', lambda m: back[int(m.group(1))] if int(m.group(1)) in back else m.group(0), s)
        if s != tree.sql(dialect='sqlite'):
            out.add(s + ';')
    return sorted(out)


def main(argv=None):
    import sqlglot as sg

    a = argparse.ArgumentParser(prog='sqlgauntlet.fuzz.mutate', description=__doc__.split('\n')[0])
    a.add_argument('corpus')
    a.add_argument('out')
    a.add_argument('apps', nargs='*')
    a.add_argument('--per', type=int, default=3)
    a.add_argument('--seed', type=int, default=1)
    o = a.parse_args(argv)
    rng = random.Random(o.seed)
    src, out = Path(o.corpus), Path(o.out)
    man = manifest(src)
    lines, total = [], 0
    for pdir in sorted((src / 'projects').iterdir()):
        if not (pdir / 'queries.sql').exists() or (o.apps and pdir.name not in o.apps):
            continue
        d = out / 'projects' / pdir.name
        if d.exists():
            shutil.rmtree(d)
        shutil.copytree(pdir, d, ignore=shutil.ignore_patterns('queries.sql', 'expected.json', 'timings.json'))
        blocks = []
        for q in parse_queries(pdir / 'queries.sql'):
            m = man.get(q['id'], {})
            if 'read' not in m.get('tags', []):
                continue
            for k, s in enumerate(mutants(sg, q['sql'], rng, o.per)):
                i = f"{q['id']}~m{k}"
                blocks.append(f'-- @id {i}\n' + ''.join(f'-- @param {n} {v}\n' for n, v in q['params']) + s + '\n')
                lines.append(json.dumps({**{x: m[x] for x in ('project', 'tags') if x in m}, 'id': i, 'sql': s, 'mutant_of': q['id']}))
                total += 1
        (d / 'queries.sql').write_text('\n'.join(blocks))
    (out / 'manifest.jsonl').write_text(''.join(line + '\n' for line in lines))
    print(f'mutate: {total} mutants (seed {o.seed}, up to {o.per} a statement) in {out}')


if __name__ == '__main__':
    main()
