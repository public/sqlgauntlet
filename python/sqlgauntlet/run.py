"""run: a corpus through several arms, interleaved. An arm is a backend and the environment it runs under:
  <backend>                                  build/backends/<backend>.so, with the environment in <backend>.json
  <name>=<backend>[:K=V:...]                 the same backend under another name, with more environment (e.g. a
                                             backend's own switches: two arms of one build)
  <name>=so:<backend.so>[:K=V:...]           any backend shared object, through this repository's driver
  <name>=exe:<program>[:K=V:...]             any program with the driver's command line (<program> <mode> <db> <file>
                                             ...) and output lines, e.g. another harness held to this one
Every variable any arm sets is removed from the inherited environment before each arm adds its own, so an arm's
switches never leak into another's.
sqlgauntlet-run check: every statement 8 times per arm (the driver's check mode) on fresh copies of one database per
  project -> <out>/<arm>.check.tsv, then compare's report.
sqlgauntlet-run speed: --rounds rounds; in each, every project's database is copied once per arm and the arms run it in
  an order rotated by round and project (so no arm is always first or always last on a warm cache), hot then prep ->
  (--interleave statement: the arms take turns statement by statement instead, each in its own process, in an order
  rotated by round, project and statement, so drift within a project's run falls on every arm alike; --aa adds
  <reference>-aa, the reference again under another name, whose ratios to the reference are the run's noise floor) ->
  <out>/<arm>.<mode>.<round>.tsv (SG_EXEC=1: per-execution exec lines, for the warm-up/steady-state split), then
  summarise's report.
--heavy-pass MS: only the corpus's slow statements, those tagged heavy and those whose manifest stock_ms_indicative is MS
  or more (default 150: corpus/tools/time.py's numbers, from the author's machine, a few times under a box's), with few
  executions (--reps 3 and --budget 0 unless given): the pass a full run leaves out or can't afford to repeat.
The box, kernel, compiler and every arm's build options and version go to <out>/env.json with the results."""

import json
import os
import platform
import shutil
import subprocess
import sys
import time
from pathlib import Path

from . import cg
from . import lines as L
from . import prep as P

ROOT = Path(__file__).resolve().parents[2]
BACKENDS = ROOT / 'build' / 'backends'
TIMEOUT = 1800.0  # (seconds a driver run may take, --timeout: past it, that run's statements are missing)
ARM_VARS = set()  # (every variable some arm sets: none inherits another's)


def fresh_copy(src, dst):
    """src over dst, and dst's -wal, -shm, -journal (SQLite) and .wal, .tmp/ (DuckDB) removed first: a sidecar left by an earlier run (a killed
    process, an engine that keeps its WAL) would otherwise be read as part of the new copy (a WAL is replayed onto
    any database file)"""
    for x in ('-wal', '-shm', '-journal', '.wal'):  # (.wal: DuckDB's)
        Path(f'{dst}{x}').unlink(missing_ok=True)
    shutil.rmtree(f'{dst}.tmp', ignore_errors=True)  # (DuckDB's spill directory)
    shutil.copyfile(src, dst)


def backend_env(name):
    j = BACKENDS / f'{name}.json'
    return json.loads(j.read_text()).get('env', {}) if j.exists() else {}


class Arm:
    def __init__(self, spec, driver):
        self.spec = spec
        name, _, rest = spec.partition('=')
        parts = (rest or name).split(':')
        if parts[0] in ('so', 'exe'):
            kind, path, env = parts[0], parts[1], {}
            extra = parts[2:]
        else:  # (a built backend, by name)
            kind, path, env = 'so', str(BACKENDS / f'{parts[0]}.so'), backend_env(parts[0])
            extra = parts[1:]
        env.update(kv.split('=', 1) for kv in extra if kv)
        ARM_VARS.update(env)
        self.name, self.kind, self.path, self.env = name, kind, os.path.abspath(path), env
        if not os.path.exists(self.path):
            sys.exit(f'sqlgauntlet-run: arm {name}: {self.path} not found (make {Path(self.path).stem}?)')
        self.cmd = [driver, '--backend', self.path] if kind == 'so' else [self.path]
        j = Path(self.path).with_suffix('.json')
        self.dialect = json.loads(j.read_text()).get('dialect', 'sqlite') if kind == 'so' and j.exists() else 'sqlite'

    def environ(self, extra=None):
        e = {k: v for k, v in os.environ.items() if k not in ARM_VARS}
        e.update(TZ='UTC', **self.env)
        e.update(extra or {})
        return e

    def run(self, args, out=None, extra=None, timeout=None, wrap=(), driver=None):
        cmd = list(wrap) + ([driver] + self.cmd[1:] if driver and self.kind == 'so' else self.cmd) + [str(a) for a in args]
        timeout = timeout or TIMEOUT
        try:
            r = subprocess.run(cmd, stdout=out or subprocess.PIPE, stderr=subprocess.PIPE, env=self.environ(extra), timeout=timeout, text=True)
        except subprocess.TimeoutExpired:  # (one project too slow: its statements show as missing, the arm goes on)
            print(f'sqlgauntlet-run: {self.name} {args[0]} {Path(str(args[-1])).name}: timed out after {timeout:.0f} s', file=sys.stderr)
            return subprocess.CompletedProcess(cmd, 124, '', 'timed out')
        if r.returncode:
            print(f'sqlgauntlet-run: {self.name} {args[0]} {Path(str(args[-1])).name}: exit {r.returncode}: {r.stderr.strip()[-300:]}', file=sys.stderr)
        return r

    def info(self):
        if self.kind == 'exe':
            return {'name': self.name, 'kind': 'exe', 'path': self.path, 'env': self.env}
        r = subprocess.run(self.cmd + ['info'], capture_output=True, text=True, env=self.environ())
        f = r.stdout.strip().split('\t')
        cf = Path(self.path + '.cflags')
        return {
            'name': self.name,
            'kind': 'so',
            'path': self.path,
            'env': self.env,
            'backend': f[1] if len(f) > 1 else '?',
            'dialect': f[2] if len(f) > 2 else '?',
            'version': f[3] if len(f) > 3 else '?',
            'cflags': cf.read_text().strip() if cf.exists() else '?',
        }


def environment(arms, corpus):
    def sh(*c):
        try:
            return subprocess.run(c, capture_output=True, text=True, timeout=10).stdout.strip()
        except Exception:
            return '?'

    cpu = (
        sh('sysctl', '-n', 'machdep.cpu.brand_string')
        if sys.platform == 'darwin'
        else next((l.split(':', 1)[1].strip() for l in open('/proc/cpuinfo') if l.startswith('model name')), '?')
    )
    cc = os.environ.get('CC', 'cc')
    return {
        'date': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()),
        'host': platform.node(),
        'system': platform.system(),
        'kernel': platform.release(),
        'machine': platform.machine(),
        'cpu': cpu,
        'ncpu': os.cpu_count(),
        'compiler': sh(cc, '--version').split('\n')[0],
        'python': platform.python_version(),
        'perf_event_paranoid': sh('cat', '/proc/sys/kernel/perf_event_paranoid') if sys.platform.startswith('linux') else None,
        'corpus': str(corpus),
        'corpus_commit': sh('git', '-C', str(corpus), 'rev-parse', 'HEAD'),
        'corpus_dirty': bool(sh('git', '-C', str(corpus), 'status', '--porcelain', '.')),
        'sqlgauntlet_commit': sh('git', '-C', str(ROOT), 'rev-parse', 'HEAD'),
        'arms': [a.info() for a in arms],
    }


def build_db(arm, db, bs, lt, ps, tmp, blocks=False):
    """db from the build script bs, then the data files in lt (table, file, format) and the script ps; the file appears
    only when every step succeeded. blocks: ps holds statements in blocks ending '-- end', each run on its own (one
    that fails is reported, the rest still run)"""
    db.unlink(missing_ok=True)
    part = db.with_suffix('.db.part')
    part.unlink(missing_ok=True)
    ok = arm.run(['build', part, bs], timeout=7200).returncode == 0
    if ok and lt.exists():  # (a workload's or an export's data files, then indexes, views and ANALYZE)
        for line in lt.read_text().splitlines():
            t, f, fmt = line.split('\t')
            ok = ok and arm.run(['load', part, t, f, fmt], timeout=7200).returncode == 0
        if ok and ps.exists() and blocks:
            for b in ps.read_text().split('\n-- end\n'):
                if b.strip() and not all(x.startswith('--') for x in b.strip().splitlines()):
                    (tmp / 'post.sql').write_text(b)
                    arm.run(['build', part, tmp / 'post.sql'], timeout=7200)
        elif ok and ps.exists():
            ok = arm.run(['build', part, ps], timeout=7200).returncode == 0
    if ok:
        part.rename(db)
    return ok


def statement_turns(arms, dirs, p, r, k, modes, o, files, extra, tmp):
    """one project's round with the arms taking turns statement by statement: each arm its own copy of the database
    (writes roll back), then per statement every arm in an order rotated by round, project and statement"""
    copies = {}
    for arm in arms:
        qa, da = dirs[arm.dialect]
        if (da / f'{p}.db').exists():
            copies[arm.name] = tmp / f'c-{len(copies)}.db'
            fresh_copy(da / f'{p}.db', copies[arm.name])
    q0 = dirs['sqlite'][0] / f'{p}.q'
    ids = [l.split()[1] for l in open(q0) if l.startswith('@@Q ')]
    n = len(arms)
    for j, sid in enumerate(ids):
        for arm in [arms[(x + r + k + j) % n] for x in range(n)]:
            if arm.name not in copies:
                continue
            qa = dirs[arm.dialect][0]
            for m in modes:
                arm.run([m, copies[arm.name], qa / f'{p}.q', o.reps, o.budget, sid], out=files[arm.name, m, r], extra=extra)


def main(argv=None):
    import argparse

    a = argparse.ArgumentParser(prog='sqlgauntlet-run', description=__doc__.split('\n')[0])
    a.add_argument('mode', choices=('check', 'speed'))
    a.add_argument('projects', nargs='*')
    a.add_argument('--corpus', default=os.environ.get('SQLGAUNTLET_CORPUS', str(ROOT / 'corpus')))
    a.add_argument('--out', default=str(ROOT / 'build' / 'run'))
    a.add_argument('--arm', action='append', help='repeatable; the first is the reference; default sqlite and libsql')
    a.add_argument('--build-with', help='the arm that builds the databases (default: the first)')
    a.add_argument('--driver', default=str(ROOT / 'build' / 'sqlgauntlet'))
    a.add_argument('--rounds', type=int, default=1)
    a.add_argument('--r0', type=int, default=1)
    a.add_argument('--reps', type=int)
    a.add_argument('--budget', type=float)
    a.add_argument('--modes', default='hot,prep')
    a.add_argument('--exec-lines', action='store_true')
    a.add_argument(
        '--extra-counters',
        action='store_true',
        help='speed: exec lines with kernel cycles, branch misses, page faults, context switches (SG_EXTRA)',
    )
    a.add_argument('--split', action='store_true', help='speed: exec lines with the time in step, column reads and reset (SG_SPLIT; implies --exec-lines)')
    a.add_argument('--min-reps', type=int, help='speed: executions each statement gets whatever the budget (SG_MIN_REPS; the driver\'s default is 3)')
    a.add_argument('--interleave', choices=('project', 'statement'), default='project', help='speed: the arms take turns by project (default) or statement')
    a.add_argument('--aa', action='store_true', help='speed: an A/A control, the reference again as <reference>-aa, for the noise floor')
    a.add_argument('--callgrind', action='store_true', help='speed: instructions from valgrind (make callgrind; so arms)')
    a.add_argument('--scale', default='1')
    a.add_argument('--ids')
    a.add_argument('--wave', default='')
    a.add_argument('--heavy', action='store_true')
    a.add_argument('--heavy-pass', type=float, nargs='?', const=150.0, metavar='MS', help='only the slow statements, few executions')
    a.add_argument('--timeout', type=float, help='seconds one driver run (an arm on a project) may take (default 1800); past it, its statements are missing')
    a.add_argument('--keep', action='store_true')
    a.add_argument('--no-report', action='store_true')
    o = a.parse_intermixed_args(argv)  # (the projects may come after the options: run check --arm a --out X <project>)
    global TIMEOUT
    TIMEOUT = o.timeout or TIMEOUT
    if o.reps is None:
        o.reps = 3 if o.heavy_pass is not None else 200
    if o.budget is None:
        o.budget = 0 if o.heavy_pass is not None else 60
    arms = [Arm(s, o.driver) for s in (o.arm or ['sqlite', 'libsql'])]
    if o.aa:  # (the reference again, second: summarise reads <reference>-aa as the noise floor)
        n0, _, r0 = arms[0].spec.partition('=')
        arms.insert(1, Arm(f'{n0}-aa={r0 or n0}', o.driver))
    if o.interleave == 'statement' and o.callgrind:
        sys.exit('sqlgauntlet-run: --interleave statement times natively; callgrind runs one process per project')
    if len({x.name for x in arms}) != len(arms):
        sys.exit('sqlgauntlet-run: arm names must differ')
    out = Path(o.out)
    q = out / 'q'
    d = out / 'db'
    tmp = out / 'tmp'
    for x in (out, d, tmp):
        x.mkdir(parents=True, exist_ok=True)
    ids = {l.split('\t')[0].split()[0] for l in open(o.ids) if l.strip() and not l.startswith('#')} if o.ids else None
    if o.heavy_pass is not None:  # (the slow statements: tagged heavy, or indicatively MS or more)
        slow = {i for i, r in P.manifest(o.corpus).items() if 'heavy' in r.get('tags', []) or (r.get('stock_ms_indicative') or 0) >= o.heavy_pass}
        ids = slow if ids is None else ids & slow
        o.heavy = True
    if ids is not None:
        shutil.rmtree(q, ignore_errors=True)
    P.prep(o.corpus, q, o.scale, o.projects, ids, o.wave, o.heavy)
    projects = sorted(p.name[:-2] for p in q.glob('*.q'))
    sq = [x for x in arms if x.dialect == 'sqlite' and x.kind == 'so']
    bw = next((x for x in arms if x.name == o.build_with), None) if o.build_with else (sq[0] if sq else None)
    if bw is None or bw.kind == 'exe' or bw.dialect != 'sqlite':
        bw = Arm('sqlite', o.driver)  # (the reference build: every other dialect's data is exported from it)
    for p in projects:
        db, bs = d / f'{p}.db', q / f'{p}.build.sql'
        if not db.exists() or db.stat().st_mtime < bs.stat().st_mtime:
            build_db(bw, db, bs, q / f'{p}.load.tsv', q / f'{p}.post.sql', tmp)
    dirs = {'sqlite': (q, d)}
    for dl in sorted({x.dialect for x in arms} - {'sqlite'}):  # (another dialect: its statements translated, its data exported)
        from . import dialect as D
        from . import export as X

        qd, dd = out / f'q-{dl}', out / f'db-{dl}'
        dd.mkdir(parents=True, exist_ok=True)
        if ids is not None:
            shutil.rmtree(qd, ignore_errors=True)
        P.prep(o.corpus, qd, o.scale, o.projects, ids, o.wave, o.heavy, dialect=dl)
        builder = next(x for x in arms if x.dialect == dl)
        for p in projects:
            db = dd / f'{p}.db'
            code = max(Path(m.__file__).stat().st_mtime for m in (X, D))
            newer = max((d / f'{p}.db').stat().st_mtime, code) if (d / f'{p}.db').exists() else None
            if newer and (not db.exists() or db.stat().st_mtime < newer):  # (re-exported when the reference or the export code changed)
                for note in X.export(d / f'{p}.db', qd, p, dl):
                    print(f'sqlgauntlet-run: export {dl}: {note}', file=sys.stderr)
                build_db(builder, db, qd / f'{p}.build.sql', qd / f'{p}.load.tsv', qd / f'{p}.post.sql', tmp, blocks=True)
        dirs[dl] = (qd, dd)
    for dl, (_, dd) in dirs.items():  # (an app a dialect couldn't build: that dialect's arms skip it, the rest run it)
        unbuilt = [p for p in projects if not (dd / f'{p}.db').exists()]
        if unbuilt:
            print(f'sqlgauntlet-run: {dl}: not built, skipped (its statements show as missing): {", ".join(unbuilt)}', file=sys.stderr)
    env = environment(arms, o.corpus)
    env.update(
        mode=o.mode,
        build_with=bw.name,
        rounds=o.rounds,
        interleave=o.interleave,
        aa=o.aa,
        heavy_pass=o.heavy_pass,
        reps=o.reps,
        budget_ms=o.budget,
        min_reps=o.min_reps,
        exec_lines=bool(o.exec_lines or o.extra_counters or o.split),
        extra_counters=o.extra_counters,
        split=o.split,
        scale=o.scale,
        statements=sum(1 for p in projects for l in open(q / f'{p}.q') if l.startswith('@@Q ')),
    )
    c = tmp / 'c.db'
    if o.mode == 'check':
        if not o.keep:
            for f in out.glob('*.check.tsv'):
                f.unlink()
        for arm in arms:
            t0 = time.time()
            with open(out / f'{arm.name}.check.tsv', 'w') as f:
                f.write(L.header('check', 'norm'))
                f.flush()
                qa, da = dirs[arm.dialect]
                for p in projects:
                    if not (da / f'{p}.db').exists():
                        continue
                    fresh_copy(da / f'{p}.db', c)
                    arm.run(['check', c, qa / f'{p}.q'], out=f)
            n = sum(1 for l in open(out / f'{arm.name}.check.tsv') if L.split(l)[0] == 'check')
            print(
                f'sqlgauntlet-run check: {arm.name}: {n} statements, {time.time() - t0:.0f} s',
                file=sys.stderr,
            )
    else:
        if not o.keep:
            for f in list(out.glob('*.hot.*.tsv')) + list(out.glob('*.prep.*.tsv')):
                f.unlink()
        modes = o.modes.split(',')
        cgdriver = str(ROOT / 'build' / 'callgrind' / 'sqlgauntlet')
        if o.callgrind and not os.path.exists(cgdriver):
            sys.exit('sqlgauntlet-run: --callgrind needs make callgrind (Linux, valgrind)')
        extra = {'SG_EXEC': '1'} if o.exec_lines or o.extra_counters or o.split else {}
        if o.extra_counters:
            extra['SG_EXTRA'] = '1'
        if o.split:
            extra['SG_SPLIT'] = '1'
        if o.min_reps:
            extra['SG_MIN_REPS'] = str(o.min_reps)
        files = {}
        for r in range(o.r0, o.r0 + o.rounds):
            t0 = time.time()
            for arm in arms:
                for m in modes:
                    files[arm.name, m, r] = open(out / f'{arm.name}.{m}.{r}.tsv', 'w')
                    files[arm.name, m, r].write(L.header('timing', 'exec', exec_more='SG_EXTRA' in extra or 'SG_SPLIT' in extra))
                    files[arm.name, m, r].flush()
            for k, p in enumerate(projects):
                n = len(arms)
                if o.interleave == 'statement':
                    statement_turns(arms, dirs, p, r, k, modes, o, files, extra, tmp)
                    continue
                order = [arms[(j + r + k) % n] for j in range(n)]
                for arm in order:
                    qa, da = dirs[arm.dialect]
                    if not (da / f'{p}.db').exists():
                        continue
                    fresh_copy(da / f'{p}.db', c)  # (one copy per project, arm and round: writes roll back)
                    for m in modes:
                        if o.callgrind and arm.kind == 'so':  # (deterministic instructions; times are valgrind's)
                            for f in tmp.glob('cg.*'):
                                f.unlink()
                            res = arm.run(
                                [m, c, qa / f'{p}.q', o.reps, o.budget],
                                extra=extra,
                                timeout=7200,
                                wrap=cg.VALGRIND + [f'--callgrind-out-file={tmp}/cg.%p'],
                                driver=cgdriver,
                            )
                            files[arm.name, m, r].writelines(cg.apply(res.stdout.splitlines(), sorted(tmp.glob('cg.*'))))
                        else:
                            arm.run([m, c, qa / f'{p}.q', o.reps, o.budget], out=files[arm.name, m, r], extra=extra)
            for f in files.values():
                f.close()
            files.clear()
            print(f'sqlgauntlet-run speed: round {r} done, {time.time() - t0:.0f} s', file=sys.stderr)
    (out / 'env.json').write_text(json.dumps(env, indent=1) + '\n')
    shutil.rmtree(tmp, ignore_errors=True)
    if o.no_report:
        return
    if o.mode == 'check':
        from . import compare

        compare.main([str(out)] + [x.name for x in arms])
    else:
        from . import summarise

        summarise.main([str(out)] + [x.name for x in arms])


if __name__ == '__main__':
    main()
