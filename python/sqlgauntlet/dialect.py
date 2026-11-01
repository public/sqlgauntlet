"""dialect: corpus text for an engine that doesn't speak SQLite's SQL. For each statement, the first that applies:
  1. an override in <corpus>/dialects/<dialect>.jsonl: {"id": ..., "sql": ...} (hand-written, reviewed), or
     {"id": ..., "untranslatable": "<why>"}
  2. sqlglot's transpiler (read="sqlite", write=<dialect>), when sqlglot is installed; a statement it rejects or only
     half-translates (unsupported warnings) is untranslatable with its message
  3. otherwise untranslatable: "no translator"
Parameters: SQLite's ?N, ?, :name, @name and $name are swapped for markers sqlglot keeps as identifiers, and come back
as the dialect's own numbered form ($1..$N for postgres), the statement's @@P list renumbered to match. sqlglot can
translate a statement into something that runs but means something else (SQLite's date functions are the usual
case): compare's verdicts against the SQLite reference are what tell. Schemas and data don't go through here: an app's
database is exported from the SQLite reference build (export.py).
Untranslatable statements are reported by compare as such, never dropped silently."""

import json
import re
from pathlib import Path

PARAM = re.compile(r"""'(?:[^']|'')*'|"(?:[^"]|"")*"|\?(\d+)|\?|(?<![:\w]):([A-Za-z_]\w*)|(?<![\w@])@([A-Za-z_]\w*)|(?<![\w$])\$([A-Za-z_]\w*)""")


def number_params(sql):
    """(sql with __sgpK__ markers, {SQLite parameter name: K}); quoted text is left alone"""
    names, anon = {}, [0]

    def sub(m):
        t = m.group(0)
        if t[0] in '\'"':
            return t
        if m.group(1):
            name = '?' + m.group(1)
        elif t == '?':
            anon[0] += 1
            name = f'?{anon[0]}'
        else:
            name = t
        k = names.setdefault(name, len(names) + 1)
        return f'__sgp{k}__'

    return PARAM.sub(sub, sql), names


def transpile(sg, sql, dialect):
    """sqlglot's translation, every identifier lowercased and quoted (SQLite's names are case-insensitive; export.py
    creates them the same way, so reserved words such as user work), and ON CONFLICT's target without the NULLS FIRST
    that sqlglot adds to every column list it reads as an ordering"""
    out = sg.transpile(sql, read='sqlite', write=dialect, unsupported_level=sg.ErrorLevel.RAISE, identify=True, normalize=True)
    fix = lambda m: re.sub(r'\s+NULLS\s+(FIRST|LAST)', '', m.group(0))
    return [re.sub(r'ON CONFLICT\s*\([^()]*\)', fix, x) for x in out]


class Translator:
    def __init__(self, corpus, dialect):
        self.dialect, self.over, self.scripts = dialect, {}, {}
        f = Path(corpus) / 'dialects' / f'{dialect}.jsonl'
        if f.exists():
            for l in open(f):
                if not l.strip():
                    continue
                r = json.loads(l)
                if 'project' in r:
                    self.scripts[r['project']] = r['script']
                else:
                    self.over[r['id']] = r
        try:
            import sqlglot

            self.sg = sqlglot
        except ImportError:
            self.sg = None

    def _glot(self, sql):
        if not self.sg:
            return None, 'no translator (pip install sqlglot)'
        try:
            out = transpile(self.sg, sql, self.dialect)
        except Exception as e:
            return None, f'sqlglot: {str(e).splitlines()[0][:160]}'
        return ';\n'.join(out) + ';', None

    def statement(self, i, sql, params=()):
        """(text, params renamed for the dialect, None) or (None, None, why). params: [(SQLite name, literal)]"""
        o = self.over.get(i)
        if o and 'untranslatable' in o:
            return None, None, 'override: ' + o['untranslatable']
        marked, names = number_params(o['sql'] if o else sql)
        if o:
            t, why = marked, None
        else:
            t, why = self._glot(marked)
        if t is None:
            return None, None, why
        t = re.sub(r'"?__sgp(\d+)__"?', lambda m: f'${m.group(1)}', t, flags=re.I)
        return t, [(f'${names[n]}', lit) for n, lit in params if n in names], None
