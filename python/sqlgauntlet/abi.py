"""abi: a backend shared object (include/sqlgauntlet.h, ABI 1) driven from Python through ctypes, for the property and
fuzz tests (fuzz/): open a database, run scripts, prepare, bind, step, read typed values, as the driver does.
Backend('build/backends/sqlite.so') loads one (RTLD_LOCAL: several builds of one engine coexist in a process); its
arm defaults (build/backends/<name>.json: environment) are applied to os.environ first, since an engine may read them
when it loads. Values come back as SQLite's five kinds, tagged: ('i', int), ('f', float), ('t', bytes), ('b', bytes),
None."""

import ctypes as C
import json
import os
from pathlib import Path

SG_INTEGER, SG_FLOAT, SG_TEXT, SG_BLOB, SG_NULL = 1, 2, 3, 4, 5
SG_ROW, SG_DONE = 100, 101


class Value(C.Structure):
    _fields_ = [('type', C.c_int), ('i', C.c_int64), ('f', C.c_double), ('p', C.c_void_p), ('n', C.c_size_t)]


class ExecStats(C.Structure):
    _fields_ = [('alt_path', C.c_int), ('engine_ms', C.c_double), ('engine_counters', C.c_int64 * 4)]


V, P = C.c_void_p, C.c_char_p
F = C.CFUNCTYPE


class Table(C.Structure):  # (sg_backend, field for field)
    _fields_ = [
        ('abi', C.c_int),
        ('name', P),
        ('dialect', P),
        ('version', F(P)),
        ('open', F(V, P, C.c_char_p, C.c_size_t)),
        ('close', F(None, V)),
        ('exec', F(C.c_int, V, P, C.c_char_p, C.c_size_t)),
        ('prepare', F(V, V, P, C.c_char_p, C.c_size_t)),
        ('param_count', F(C.c_int, V)),
        ('param_index', F(C.c_int, V, P)),
        ('bind', F(C.c_int, V, C.c_int, C.POINTER(Value))),
        ('step', F(C.c_int, V)),
        ('column_count', F(C.c_int, V)),
        ('column', F(None, V, C.c_int, C.POINTER(Value))),
        ('reset', F(C.c_int, V)),
        ('finalize', F(None, V)),
        ('changes', F(C.c_int64, V)),
        ('errmsg', F(P, V)),
        ('exec_stats', F(None, V, C.POINTER(ExecStats))),
        ('exec_begin', F(None, V)),
        ('counter_names', P),
        ('explain', F(P, V, P)),
    ]


class Error(Exception):
    pass


def tag(v):
    """a Value as a tagged Python value"""
    if v.type == SG_INTEGER:
        return ('i', v.i)
    if v.type == SG_FLOAT:
        return ('f', v.f)
    if v.type in (SG_TEXT, SG_BLOB):
        return ('t' if v.type == SG_TEXT else 'b', C.string_at(v.p, v.n) if v.n else b'')
    return None


def untag(x):
    """a Python value (int, float, str, bytes, None, or a tagged one) as a Value"""
    v, keep = Value(), None
    if isinstance(x, tuple):
        x = x[1].decode('utf-8', 'surrogateescape') if x[0] == 't' else x[1]
    if x is None:
        v.type = SG_NULL
    elif isinstance(x, bool) or isinstance(x, int):
        v.type, v.i = SG_INTEGER, int(x)
    elif isinstance(x, float):
        v.type, v.f = SG_FLOAT, x
    else:
        b = x if isinstance(x, bytes) else x.encode('utf-8', 'surrogatepass')
        keep = C.create_string_buffer(b, len(b))
        v.type, v.p, v.n = (SG_BLOB if isinstance(x, bytes) else SG_TEXT), C.cast(keep, C.c_void_p), len(b)
    return v, keep


class Backend:
    def __init__(self, so, env=None):
        so = Path(so)
        j = so.with_suffix('.json')
        os.environ.update(json.loads(j.read_text()).get('env', {}) if j.exists() else {})
        os.environ.update(env or {})
        self.lib = C.CDLL(str(so), mode=os.RTLD_LOCAL | os.RTLD_NOW)
        fn = self.lib.sqlgauntlet_backend_v1
        fn.restype = C.POINTER(Table)
        self.t = fn().contents
        if self.t.abi != 1:
            raise Error(f'{so}: ABI {self.t.abi}')
        self.name, self.dialect = self.t.name.decode(), self.t.dialect.decode()

    def version(self):
        return self.t.version().decode() if self.t.version else '?'

    def open(self, path):
        return Conn(self, path)


class Conn:
    def __init__(self, b, path):
        self.b, self.t = b, b.t
        err = C.create_string_buffer(512)
        self.c = self.t.open(str(path).encode(), err, 512)
        if not self.c:
            raise Error(err.value.decode(errors='replace'))

    def close(self):
        if self.c:
            self.t.close(self.c)
            self.c = None

    def __enter__(self):
        return self

    def __exit__(self, *a):
        self.close()

    def exec(self, sql):
        err = C.create_string_buffer(512)
        if self.t.exec(self.c, sql.encode('utf-8', 'surrogatepass'), err, 512):
            raise Error(err.value.decode(errors='replace'))

    def prepare(self, sql):
        return Stmt(self, sql)

    def query(self, sql, params=(), runs=1):
        """[(rows, error)] for <runs> executions of one prepared statement; rows: a list of tuples of tagged values"""
        try:
            s = self.prepare(sql)
        except Error as e:
            return [(None, f'prepare: {e}')] * runs
        try:
            s.bind(params)
            return [s.run() for _ in range(runs)]
        finally:
            s.finalize()


class Stmt:
    def __init__(self, conn, sql):
        self.conn, self.t = conn, conn.t
        err = C.create_string_buffer(512)
        self.s = self.t.prepare(conn.c, sql.encode('utf-8', 'surrogatepass'), err, 512)
        if not self.s:
            raise Error(err.value.decode(errors='replace'))
        self.keep = []

    def bind(self, params):
        for k, x in enumerate(params, 1):
            v, keep = untag(x)
            self.keep.append(keep)
            self.t.bind(self.s, k, C.byref(v))

    def run(self):
        """(rows, error) for one execution, then reset (bindings kept)"""
        if self.t.exec_begin:
            self.t.exec_begin(self.s)
        rows, v, rc = [], Value(), None
        nc = self.t.column_count(self.s)
        while True:
            rc = self.t.step(self.s)
            if rc != SG_ROW:
                break
            row = []
            for i in range(nc):
                self.t.column(self.s, i, C.byref(v))
                row.append(tag(v))
            rows.append(tuple(row))
        e = None if rc == SG_DONE else (self.t.errmsg(self.conn.c) or b'error').decode(errors='replace')
        self.t.reset(self.s)
        return (rows if e is None else None), e

    def finalize(self):
        if self.s:
            self.t.finalize(self.s)
            self.s = None
