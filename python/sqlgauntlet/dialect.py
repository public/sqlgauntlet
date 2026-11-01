"""dialect: corpus text for an engine that doesn't speak SQLite's SQL. For each statement, the first that applies:
  1. an override in <corpus>/dialects/<dialect>.jsonl: {"id": ..., "sql": ...} (hand-written, reviewed), or
     {"id": ..., "untranslatable": "<why>"}
  2. sqlglot's transpiler (read="sqlite", write=<dialect>), when sqlglot is installed; a statement it rejects or only
     half-translates (unsupported warnings) is untranslatable with its message
  3. otherwise untranslatable: "no translator"
The schema and data scripts go through the same path, by project: overrides {"project": ..., "script": ...}.
Untranslatable statements are reported by compare as such, never dropped silently."""
import json
from pathlib import Path


class Translator:
    def __init__(self, corpus, dialect):
        self.dialect, self.over, self.scripts = dialect, {}, {}
        f = Path(corpus) / 'dialects' / f'{dialect}.jsonl'
        if f.exists():
            for l in open(f):
                if not l.strip(): continue
                r = json.loads(l)
                if 'project' in r: self.scripts[r['project']] = r['script']
                else: self.over[r['id']] = r
        try:
            import sqlglot; self.sg = sqlglot
        except ImportError:
            self.sg = None

    def _glot(self, sql):
        if not self.sg: return None, 'no translator (pip install sqlglot)'
        try:
            out = self.sg.transpile(sql, read='sqlite', write=self.dialect, unsupported_level=self.sg.ErrorLevel.RAISE)
        except Exception as e:
            return None, f'sqlglot: {str(e).splitlines()[0][:160]}'
        return ';\n'.join(out) + ';', None

    def statement(self, i, sql):
        """(text, None) or (None, why)"""
        o = self.over.get(i)
        if o: return (o['sql'], None) if 'sql' in o else (None, 'override: ' + o['untranslatable'])
        return self._glot(sql)

    def script(self, project, sql):
        if project in self.scripts: return self.scripts[project]
        t, why = self._glot(sql)
        return t if t else f'-- untranslatable: {why}\n'
