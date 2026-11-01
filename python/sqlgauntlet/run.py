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
  <out>/<arm>.<mode>.<round>.tsv (SG_EXEC=1: per-execution E lines, for the warm-up/steady-state split), then
  summarise's report.
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
from . import prep as P

ROOT = Path(__file__).resolve().parents[2]
BACKENDS = ROOT / 'build' / 'backends'
ARM_VARS = set()  # (every variable some arm sets: none inherits another's)


def backend_env(name):
    j = BACKENDS / f'{name}.json'
    return json.loads(j.read_text()).get('env', {}) if j.exists() else {}


class Arm:
    def __init__(self, spec, driver):
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

    def environ(self, extra=None):
        e = {k: v for k, v in os.environ.items() if k not in ARM_VARS}
        e.update(TZ='UTC', **self.env)
        e.update(extra or {})
        return e

    def run(self, args, out=None, extra=None, timeout=1800, wrap=(), driver=None):
        cmd = list(wrap) + ([driver] + self.cmd[1:] if driver and self.kind == 'so' else self.cmd) + [str(a) for a in args]
        r = subprocess.run(cmd, stdout=out or subprocess.PIPE, stderr=subprocess.PIPE, env=self.environ(extra), timeout=timeout, text=True)
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
    a.add_argument('--reps', type=int, default=200)
    a.add_argument('--budget', type=float, default=60)
    a.add_argument('--modes', default='hot,prep')
    a.add_argument('--exec-lines', action='store_true')
    a.add_argument('--callgrind', action='store_true', help='speed: instructions from valgrind (make callgrind; so arms)')
    a.add_argument('--scale', default='1')
    a.add_argument('--ids')
    a.add_argument('--wave', default='')
    a.add_argument('--heavy', action='store_true')
    a.add_argument('--keep', action='store_true')
    a.add_argument('--no-report', action='store_true')
    o = a.parse_args(argv)
    arms = [Arm(s, o.driver) for s in (o.arm or ['sqlite', 'libsql'])]
    if len({x.name for x in arms}) != len(arms):
        sys.exit('sqlgauntlet-run: arm names must differ')
    out = Path(o.out)
    q = out / 'q'
    d = out / 'db'
    tmp = out / 'tmp'
    for x in (out, d, tmp):
        x.mkdir(parents=True, exist_ok=True)
    ids = {l.split('\t')[0].split()[0] for l in open(o.ids) if l.strip() and not l.startswith('#')} if o.ids else None
    if ids is not None:
        shutil.rmtree(q, ignore_errors=True)
    P.prep(o.corpus, q, o.scale, o.projects, ids, o.wave, o.heavy)
    projects = sorted(p.name[:-2] for p in q.glob('*.q'))
    bw = next((x for x in arms if x.name == o.build_with), arms[0]) if o.build_with else arms[0]
    if bw.kind == 'exe':
        bw = Arm('sqlite', o.driver)  # (a foreign program may not build: SQLite does)
    for p in projects:
        db, bs = d / f'{p}.db', q / f'{p}.build.sql'
        if not db.exists() or db.stat().st_mtime < bs.stat().st_mtime:
            db.unlink(missing_ok=True)
            part = db.with_suffix('.db.part')
            part.unlink(missing_ok=True)
            ok = bw.run(['build', part, bs]).returncode == 0
            lt, ps = q / f'{p}.load.tsv', q / f'{p}.post.sql'
            if ok and lt.exists():  # (a workload's data files, then its indexes and ANALYZE)
                for line in lt.read_text().splitlines():
                    t, f, fmt = line.split('\t')
                    ok = ok and bw.run(['load', part, t, f, fmt], timeout=7200).returncode == 0
                ok = ok and bw.run(['build', part, ps], timeout=7200).returncode == 0
            if ok:
                part.rename(db)
    unbuilt = [p for p in projects if not (d / f'{p}.db').exists()]
    if unbuilt:  # (their statements show as missing in compare's report)
        print(f'sqlgauntlet-run: not built, skipped: {", ".join(unbuilt)}', file=sys.stderr)
        projects = [p for p in projects if p not in unbuilt]
    env = environment(arms, o.corpus)
    env.update(
        mode=o.mode,
        build_with=bw.name,
        rounds=o.rounds,
        reps=o.reps,
        budget_ms=o.budget,
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
                for p in projects:
                    shutil.copyfile(d / f'{p}.db', c)
                    arm.run(['check', c, q / f'{p}.q'], out=f)
            n = sum(1 for l in open(out / f'{arm.name}.check.tsv') if l.startswith('C'))
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
        extra = {'SG_EXEC': '1'} if o.exec_lines else {}
        files = {}
        for r in range(o.r0, o.r0 + o.rounds):
            t0 = time.time()
            for arm in arms:
                for m in modes:
                    files[arm.name, m, r] = open(out / f'{arm.name}.{m}.{r}.tsv', 'w')
            for k, p in enumerate(projects):
                n = len(arms)
                order = [arms[(j + r + k) % n] for j in range(n)]
                for arm in order:
                    shutil.copyfile(d / f'{p}.db', c)  # (one copy per project, arm and round: writes roll back)
                    for m in modes:
                        if o.callgrind and arm.kind == 'so':  # (deterministic instructions; times are valgrind's)
                            for f in tmp.glob('cg.*'):
                                f.unlink()
                            res = arm.run(
                                [m, c, q / f'{p}.q', o.reps, o.budget],
                                extra=extra,
                                timeout=7200,
                                wrap=cg.VALGRIND + [f'--callgrind-out-file={tmp}/cg.%p'],
                                driver=cgdriver,
                            )
                            files[arm.name, m, r].writelines(cg.apply(res.stdout.splitlines(), sorted(tmp.glob('cg.*'))))
                        else:
                            arm.run([m, c, q / f'{p}.q', o.reps, o.budget], out=files[arm.name, m, r], extra=extra)
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
