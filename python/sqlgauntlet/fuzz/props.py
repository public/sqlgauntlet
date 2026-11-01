"""props: Hypothesis property tests over every backend, through the backend ABI (abi.py), on generated schemas, data and
queries (strategies.py). Run with pytest: python -m pytest python/sqlgauntlet/fuzz/props.py.
  SG_FUZZ_BACKENDS  the backends under test (default: every SQLite-dialect one built: turbo libsql turso); the
                    reference is always sqlite (build/backends/sqlite.so)
  SG_FUZZ_PROFILE   dev (default: 40 examples a property, for iterating) or full (2,000); HYPOTHESIS_SEED as usual
Oracles, each a property per backend:
  differential  the backend's rows equal SQLite's (normalised: numbers as one kind, floats to 12 significant digits;
                in order when the query orders every output column, as a multiset otherwise); an error on one side only
                is a failure. A known class passes as an event unless SG_FUZZ_STRICT=1: text that differs only in
                how a number was formatted (tests/regressions/real-to-text-*)
  tlp           ternary logic partitioning: Q's rows = Q WHERE p + Q WHERE NOT p + Q WHERE p IS NULL (multisets)
  norec         SELECT count(*) WHERE p = the count of rows where p is true, computed without a WHERE
  index         the same rows after an index on every column and ANALYZE
  repeat        one prepared statement executed 8 times, and prepared afresh each time: every run the same (an
                adaptive engine switches paths between runs: turbo's adapt)
  params        a query with ?1 bound = the same query with the value inlined
A failing case is written to build/fuzz/failures/ (schema, data, query, parameters, backend, both results) before the
assertion, so it survives shrinking; tools/fuzz-reduce.py minimises one, and tests/regressions/ keeps the confirmed
ones (tests/regressions.py replays them)."""

import hashlib
import json
import os
from pathlib import Path

import pytest
from hypothesis import HealthCheck, assume, event, given, settings
from hypothesis import strategies as st

from ..abi import Backend
from .norm import loose, rows_key
from .strategies import datasets, ddl, lit, queries, schemas

R = Path(__file__).resolve().parents[3]
BK = R / 'build' / 'backends'
settings.register_profile('dev', max_examples=40, deadline=None, suppress_health_check=list(HealthCheck))
settings.register_profile('full', max_examples=2000, deadline=None, suppress_health_check=list(HealthCheck))
settings.load_profile(os.environ.get('SG_FUZZ_PROFILE', 'dev'))

_loaded = {}


def backend(name):
    if name not in _loaded:
        _loaded[name] = Backend(BK / f'{name}.so')
    return _loaded[name]


NAMES = [n for n in os.environ.get('SG_FUZZ_BACKENDS', 'turbo libsql turso').split() if (BK / f'{n}.so').exists()]


def setup(b, tables, data, extra=()):
    """a fresh in-memory database; a schema statement the engine refuses (a feature it lacks, e.g. WITHOUT ROWID)
    rejects the case, recorded as an event: a gap, not a wrong answer. Refused rows are left to the results."""
    c = b.open(':memory:')
    for s in ddl(tables) + [i for t in tables for i in t['indexes']] + list(extra):
        try:
            c.exec(s)
        except Exception as e:
            event(f'{b.name} refuses: {str(e)[:60]}')
            c.close()
            assume(False)
    for s in data:
        try:
            c.exec(s)
        except Exception:
            pass
    return c


def save(kind, name, tables, data, sql, params, got):
    case = {
        'oracle': kind,
        'backend': name,
        'schema': ddl(tables) + [i for t in tables for i in t['indexes']],
        'data': data,
        'sql': sql,
        'params': [lit(p) for p in params],
        'got': repr(got)[:4000],
    }
    d = R / 'build' / 'fuzz' / 'failures'
    d.mkdir(parents=True, exist_ok=True)
    h = hashlib.sha1(json.dumps(case, sort_keys=True).encode()).hexdigest()[:12]
    (d / f'{kind}-{name}-{h}.json').write_text(json.dumps(case, indent=1))


def case():
    return st.data()


def draw_case(data):
    tables = data.draw(schemas(), label='schema')
    rows = data.draw(datasets(tables), label='data')
    q = data.draw(queries(tables), label='query')
    return tables, rows, q


def run1(b, tables, rows, sql, params, runs=1, extra=()):
    with setup(b, tables, rows, extra) as c:
        return c.query(sql, params, runs=runs)


@pytest.mark.parametrize('name', NAMES)
@given(data=case())
def test_differential(name, data):
    tables, rows, (sql, params, _, _, _) = draw_case(data)
    ordered = ' ORDER BY ' in sql
    ((ref_rows, ref_err),) = run1(backend('sqlite'), tables, rows, sql, params)
    ((got_rows, got_err),) = run1(backend(name), tables, rows, sql, params)
    ok = (ref_err is None) == (got_err is None) and (ref_err is not None or rows_key(ref_rows, ordered) == rows_key(got_rows, ordered))
    if not ok and ref_err is None and got_err is None and not os.environ.get('SG_FUZZ_STRICT'):
        if rows_key(loose(ref_rows), ordered) == rows_key(loose(got_rows), ordered):  # (a known class: tests/regressions/real-to-text-*)
            event(f'{name}: real-to-text formatting differs')
            ok = True
    if not ok:
        save('differential', name, tables, rows, sql, params, {'sqlite': (ref_rows, ref_err), name: (got_rows, got_err)})
    assert ok, f'{name} differs from sqlite on {sql!r} {params!r}: {got_err or ""} / {ref_err or ""}'


def tlp_parts(src, where, select='*'):
    return [f'SELECT {select} FROM {src}'] + [f'SELECT {select} FROM {src} WHERE {p}' for p in (f'({where})', f'NOT ({where})', f'({where}) IS NULL')]


@pytest.mark.parametrize('name', ['sqlite'] + NAMES)
@given(data=case())
def test_tlp(name, data):
    tables, rows, (_, _, where, src, _) = draw_case(data)
    if not where:
        return
    whole, *parts = tlp_parts(src, where)
    with setup(backend(name), tables, rows) as c:
        ((a, ea),) = c.query(whole)
        res = [c.query(p)[0] for p in parts]
    if ea or any(e for _, e in res):
        return  # (an error is the differential oracle's business)
    union = [r for rr, _ in res for r in rr]
    ok = rows_key(a, False) == rows_key(union, False)
    if not ok:
        save('tlp', name, tables, rows, whole + ' -- p: ' + where, [], {'whole': len(a), 'parts': [len(r) for r, _ in res]})
    assert ok, f'{name}: TLP over {where!r} on {src}: {len(a)} rows, partitions {[len(r) for r, _ in res]}'


@pytest.mark.parametrize('name', ['sqlite'] + NAMES)
@given(data=case())
def test_norec(name, data):
    tables, rows, (_, _, where, src, _) = draw_case(data)
    if not where:
        return
    with setup(backend(name), tables, rows) as c:
        ((a, ea),) = c.query(f'SELECT count(*) FROM {src} WHERE {where}')
        ((b, eb),) = c.query(f'SELECT sum(CASE WHEN ({where}) THEN 1 ELSE 0 END) FROM {src}')
    if ea or eb:
        return
    n1, n2 = a[0][0][1], (b[0][0] or ('i', 0))[1]
    if n1 != n2:
        save('norec', name, tables, rows, f'SELECT count(*) FROM {src} WHERE {where}', [], {'count': n1, 'sum': n2})
    assert n1 == n2, f'{name}: NoREC over {where!r}: count {n1}, unfiltered sum {n2}'


@pytest.mark.parametrize('name', ['sqlite'] + NAMES)
@given(data=case())
def test_index(name, data):
    tables, rows, (sql, params, _, _, _) = draw_case(data)
    ordered = ' ORDER BY ' in sql
    extra = [f"CREATE INDEX x_{t['name']}_{c} ON {t['name']}({c})" for t in tables for c, _ in t['cols']] + ['ANALYZE']
    ((a, ea),) = run1(backend(name), tables, rows, sql, params)
    ((b, eb),) = run1(backend(name), tables, rows, sql, params, extra=extra)
    ok = (ea is None) == (eb is None) and (ea is not None or rows_key(a, ordered) == rows_key(b, ordered))
    if not ok:
        save('index', name, tables, rows, sql, params, {'plain': (a, ea), 'indexed': (b, eb)})
    assert ok, f'{name}: an index on every column and ANALYZE changed {sql!r}'


@pytest.mark.parametrize('name', ['sqlite'] + NAMES)
@given(data=case())
def test_repeat(name, data):
    tables, rows, (sql, params, _, _, _) = draw_case(data)
    ordered = ' ORDER BY ' in sql
    with setup(backend(name), tables, rows) as c:
        hot = c.query(sql, params, runs=8)
        fresh = [c.query(sql, params)[0] for _ in range(3)]
    keys = {(e or '') + repr(rows_key(r, ordered) if r is not None else None) for r, e in hot + fresh}
    if len(keys) != 1:
        save('repeat', name, tables, rows, sql, params, {'hot': hot, 'fresh': fresh})
    assert len(keys) == 1, f'{name}: {sql!r} gave {len(keys)} different results over 8 hot and 3 fresh runs'


@pytest.mark.parametrize('name', ['sqlite'] + NAMES)
@given(data=case())
def test_params(name, data):
    tables, rows, (sql, params, _, _, _) = draw_case(data)
    if not params:
        return
    inlined = sql.replace('?1', lit(params[0]))
    ordered = ' ORDER BY ' in sql
    with setup(backend(name), tables, rows) as c:
        ((a, ea),) = c.query(sql, params)
        ((b, eb),) = c.query(inlined)
    ok = (ea is None) == (eb is None) and (ea is not None or rows_key(a, ordered) == rows_key(b, ordered))
    if not ok:
        save('params', name, tables, rows, sql, params, {'bound': (a, ea), 'inlined': (b, eb)})
    assert ok, f'{name}: {sql!r} bound {params!r} differs from inlined'
