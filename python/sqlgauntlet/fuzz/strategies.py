"""strategies: Hypothesis strategies for SQLite-dialect schemas, data and queries, so that the same generated case runs
on every backend (fuzz/props.py). Everything is drawn as plain data and rendered to SQL here, so a failing case
shrinks to a small schema, few rows and a short query.
  schema   1-3 tables: columns with or without a declared type (INTEGER, REAL, TEXT, BLOB, NUMERIC, none: every
           affinity), a rowid or WITHOUT ROWID table (with a primary key), indexes on columns or expressions,
           partial ones too
  data     rows of awkward values: int64 edges, 0 and -0.0, tiny and huge doubles, '' and long text, text that looks
           like a number, NUL bytes, invalid UTF-8 as a blob, NULL anywhere
  query    a SELECT over one table or a join of two: a WHERE built from comparisons, IS [NOT] NULL, LIKE, GLOB, IN, BETWEEN,
           AND/OR/NOT; optionally GROUP BY with aggregates and HAVING, a window function, a CTE, DISTINCT, ORDER BY
           every output column, LIMIT/OFFSET; literals may become ?N parameters"""

import math
import re

from hypothesis import strategies as st

TYPES = ['INTEGER', 'REAL', 'TEXT', 'BLOB', 'NUMERIC', '']
INT_EDGES = [0, 1, -1, 2**31 - 1, -(2**31), 2**53, 2**63 - 1, -(2**63)]
FLOAT_EDGES = [0.0, -0.0, 0.5, -1.5, 1e-300, -1e300, 1.7976931348623157e308, 5e-324, 2.0**53 + 1]

values = st.one_of(
    st.none(),
    st.sampled_from(INT_EDGES),
    st.integers(-1000, 1000),
    st.sampled_from(FLOAT_EDGES),
    st.floats(allow_nan=False, allow_infinity=False, width=64),
    st.sampled_from(['', ' ', 'a', 'A', 'abc', '%', '_', '10', '1e3', ' 7', '0x10', 'é', 'ß', '☃', 'NULL']),
    st.text(max_size=8),
    st.text(min_size=200, max_size=400).map(lambda s: s or 'x' * 300),
    st.binary(max_size=6),  # (invalid UTF-8 included: as a blob)
    st.sampled_from([b'\x00', b'\xff\xfe', b'abc']),
)


def lit(v):
    """a Python value as an SQL literal"""
    if v is None:
        return 'NULL'
    if isinstance(v, bool):
        return str(int(v))
    if isinstance(v, int):
        return str(v) if v != -(2**63) else '(-9223372036854775807 - 1)'
    if isinstance(v, float):
        if v == 0.0:
            return '-0.0' if math.copysign(1, v) < 0 else '0.0'
        return repr(v)
    if isinstance(v, bytes):
        return "x'" + v.hex() + "'"
    return "'" + v.replace("'", "''") + "'"


@st.composite
def schemas(draw):
    """[{name, cols: [(name, type)], without_rowid, pk, indexes: [sql]}]"""
    tables = []
    for t in range(draw(st.integers(1, 3))):
        n = draw(st.integers(1, 4))
        cols = [(f'c{i}', draw(st.sampled_from(TYPES))) for i in range(n)]
        wr = draw(st.booleans())
        pk = [cols[0][0]] if wr else None
        idx = []
        for k in range(draw(st.integers(0, 2))):
            c = draw(st.sampled_from([c for c, _ in cols]))
            expr = draw(st.sampled_from([c, f'{c} DESC', f'lower({c})', f'{c} + 1', f'length({c})']))
            where = draw(st.sampled_from(['', f' WHERE {c} IS NOT NULL', f' WHERE {c} > 0']))
            uniq = 'UNIQUE ' if draw(st.booleans()) and not where and k == 0 and not wr else ''
            idx.append(f'CREATE {uniq}INDEX t{t}_i{k} ON t{t}({expr}){where}')
        tables.append({'name': f't{t}', 'cols': cols, 'without_rowid': wr, 'pk': pk, 'indexes': idx})
    return tables


def ddl(tables):
    out = []
    for t in tables:
        cols = ', '.join(f'{c} {ty}'.strip() for c, ty in t['cols'])
        if t['without_rowid']:
            out.append(f"CREATE TABLE {t['name']}({cols}, PRIMARY KEY({', '.join(t['pk'])})) WITHOUT ROWID")
        else:
            out.append(f"CREATE TABLE {t['name']}({cols})")
    return out


@st.composite
def datasets(draw, tables):
    """INSERT statements; WITHOUT ROWID primary keys kept unique and non-NULL, UNIQUE indexes may reject a row (the
    INSERT is OR IGNORE, as the engines must agree on that too)"""
    out = []
    for t in tables:
        seen = set()
        for _ in range(draw(st.integers(0, 12))):
            row = [draw(values) for _ in t['cols']]
            if t['without_rowid']:
                key = row[0]
                if key is None or repr(key) in seen:
                    continue
                seen.add(repr(key))
            out.append(f"INSERT OR IGNORE INTO {t['name']} VALUES ({', '.join(lit(v) for v in row)})")
    return out


@st.composite
def predicates(draw, cols, depth=0):
    c = draw(st.sampled_from(cols))
    v = draw(values)
    leaf = st.sampled_from(
        [
            f'{c} = {lit(v)}',
            f'{c} < {lit(v)}',
            f'{c} >= {lit(v)}',
            f'{c} <> {lit(v)}',
            f'{c} IS NULL',
            f'{c} IS NOT NULL',
            f'{c} IS {lit(v)}',
            f"{c} LIKE '%a%'",
            f"{c} LIKE 'A_'",
            f"{c} GLOB '*1*'",
            f'{c} IN ({lit(v)}, 1, NULL)',
            f'{c} BETWEEN -1 AND 10',
            f'{c} + 1 > 0',
            f'length({c}) > 2',
            f'{c}',
        ]
    )
    if depth >= 2 or draw(st.booleans()):
        return draw(leaf)
    a, b = draw(predicates(cols, depth + 1)), draw(predicates(cols, depth + 1))
    return draw(st.sampled_from([f'({a}) AND ({b})', f'({a}) OR ({b})', f'NOT ({a})']))


@st.composite
def queries(draw, tables):
    """(sql, params, where): params are the ?N values; where is the WHERE predicate alone (the TLP and NoREC oracles
    rewrite it), or None"""
    t = draw(st.sampled_from(tables))
    cols = [f"{t['name']}.{c}" for c, _ in t['cols']]
    src = t['name']
    if len(tables) > 1 and draw(st.booleans()):
        u = draw(st.sampled_from([x for x in tables if x is not t]))
        kind = draw(st.sampled_from(['JOIN', 'LEFT JOIN', 'CROSS JOIN']))
        on = f" ON {t['name']}.{t['cols'][0][0]} = {u['name']}.{u['cols'][0][0]}" if kind != 'CROSS JOIN' else ''
        src = f"{t['name']} {kind} {u['name']}{on}"
        cols += [f"{u['name']}.{c}" for c, _ in u['cols']]
    where = draw(predicates(cols)) if draw(st.booleans()) else None
    shape = draw(st.sampled_from(['plain', 'group', 'window', 'distinct', 'cte']))
    if shape == 'group':
        g = draw(st.sampled_from(cols))
        agg = draw(st.sampled_from(['count(*)', f'sum({cols[0]})', f'min({g})', f'max({cols[-1]})', f'total({cols[0]})', f'group_concat({g})']))
        out = [g, agg]
        tail = f' GROUP BY {g}' + (f' HAVING {agg} IS NOT NULL' if draw(st.booleans()) else '')
    elif shape == 'window':
        c = draw(st.sampled_from(cols))
        w = draw(st.sampled_from(['row_number()', f'sum({c})', f'lag({c})', 'rank()', f'max({c})']))
        out = [c, f'{w} OVER (ORDER BY {c}, rowid)' if not t['without_rowid'] and src == t['name'] else f'{w} OVER (ORDER BY {c})']
        tail = ''
    else:
        out = draw(st.lists(st.sampled_from(cols), min_size=1, max_size=3))
        tail = ''
    params = []
    body = f"SELECT {'DISTINCT ' if shape == 'distinct' else ''}{', '.join(out)} FROM {src}"
    if where:
        body += f' WHERE {where}'
    body += tail
    if shape == 'cte':
        body = f'WITH q AS ({body}) SELECT * FROM q'
    order = ''
    if draw(st.booleans()):
        n = len(out) if shape != 'cte' else len(out)
        order = ' ORDER BY ' + ', '.join(str(k) for k in range(1, n + 1))
        if draw(st.booleans()):
            order += f' LIMIT {draw(st.integers(0, 5))}' + (f' OFFSET {draw(st.integers(0, 3))}' if draw(st.booleans()) else '')
    if draw(st.booleans()) and where:  # (one literal of the predicate as a parameter: a whole token, in the WHERE only)
        v = draw(values)
        m = re.search(r'(?<![\w.?])' + re.escape(lit(v)) + r'(?![\w.])', where)
        if m:
            w2 = where[: m.start()] + '?1' + where[m.end() :]
            body = body.replace(f' WHERE {where}', f' WHERE {w2}', 1)
            params = [v]
    return body + order, params, where, src, shape
