"""norm: result rows normalised for comparing engines, as the driver's N lines are (no dependencies: tests use it)"""

import decimal
import math


def norm(v):
    """a tagged value, normalised as the driver's N lines are"""
    if v is None:
        return None
    k, x = v
    if k == 'f':
        if math.isfinite(x) and x == math.floor(x) and abs(x) <= 2**53:
            return ('n', int(x))
        return ('n', float(f'{x:.12g}'))
    if k == 'i':
        return ('n', x) if abs(x) <= 2**53 else ('n', float(f'{float(x):.12g}'))
    return (k, x)


def rows_key(rows, ordered):
    r = [tuple(norm(v) for v in row) for row in rows]
    return r if ordered else sorted(r, key=repr)


def loose(rows):
    """rows with text that reads as a number compared by its value (12 significant digits): engines format a real
    stored as text differently (SQLite 3.54's round-trip digits, older SQLite's 15, Turso's exponent form)"""

    def num(x):  # (the text's own digits, rounded: no detour through a double, which may overflow)
        try:
            d = decimal.Decimal(x)
            return format(+decimal.Context(prec=12).create_decimal(d), 'e') if d.is_finite() else None
        except (ValueError, decimal.InvalidOperation):
            return None

    def t(v):
        if v and v[0] == 't':
            try:
                parts = v[1].decode().split(',')  # (group_concat's lists too)
            except UnicodeDecodeError:
                return v
            ns = [num(x) for x in parts]
            if any(n is not None for n in ns):
                return ('tn', ','.join(n if n is not None else x for n, x in zip(ns, parts)))
        return v

    return [tuple(t(v) for v in row) for row in rows]
