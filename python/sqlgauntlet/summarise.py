"""summarise: the speed runs' <arm>.<mode>.<round>.tsv against the first arm's (the reference), per mode.
- A statement's ratio is arm / reference of its means over rounds (time: mean ms per execution; user instructions
  and cycles when the counters ran), with a 95 % percentile-bootstrap interval over rounds (each arm's rounds drawn
  independently, 1,000 draws). A group's ratio is the geometric mean of its statements', bootstrapped the same way.
- Distributions: the per-statement time ratios' percentiles and how many are over 1.02 or under 0.98.
- Warm-up vs steady state (with per-execution E lines, sqlgauntlet-run --exec-lines): an execution belongs to the
  warm-up while the arm is still deciding, i.e. up to the last one whose served flag differs from the one before
  (turbo's adapt trying the interpreter and its plan before its verdict); the rest is the steady state. The first,
  untimed execution (prepare and first run, T's first-ms) is reported apart. Steady-state ratios count only
  steady-state executions, and only for statements with at least 3 of them on both sides: fewer measures warm-up.
- Left out, and listed: statements whose rows differ from the reference's (unless they read the clock), that error,
  or that time at 0.
Markdown on stdout."""

import collections
import glob
import math
import os
import random
import statistics
import sys

B = 1000


def gm(v):
    return math.exp(sum(math.log(x) for x in v) / len(v)) if v else float('nan')


def ci(s):
    s = sorted(s)
    return s[int(0.025 * len(s))], s[int(0.975 * len(s)) - 1]


def load_q(d):
    tags, app = {}, {}
    for f in glob.glob(os.path.join(d, 'q', '*.q')):
        for l in open(f):
            if l.startswith('@@Q '):
                p = l.split()
                tags[p[1]] = p[4].split(',')
                app[p[1]] = os.path.basename(f)[:-2]
    return tags, app


def load(d, arm, mode):
    T, E = {}, {}  # id -> round -> (ms, instr, cycles, rows, hash, served, runs, err, first); id -> round -> [(served, ms, ins, cyc)]
    for f in glob.glob(os.path.join(d, f'{arm}.{mode}.*.tsv')):
        rnd = int(f.rsplit('.', 2)[-2])
        for l in open(f, errors='replace'):
            p = l.rstrip('\n').split('\t')
            if p[0] == 'T' and len(p) >= 12:
                sv, n = p[10].split('/')
                T.setdefault(p[1], {})[rnd] = (float(p[6]), float(p[8]), float(p[9]), p[2], p[3], int(sv), int(n), p[11], float(p[4]))
            elif p[0] == 'E' and len(p) >= 7:
                E.setdefault(p[1], {}).setdefault(rnd, []).append((int(p[3]), float(p[4]), float(p[5]), float(p[6])))
    return T, E


def split(ex):
    """(warm-up executions, steady-state executions): the warm-up ends at the last change of the served flag"""
    last = 0
    for k in range(1, len(ex)):
        if ex[k][0] != ex[k - 1][0]:
            last = k
    return ex[:last], ex[last:]


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    d = argv[0]
    arms = argv[1:]
    if not arms:
        sys.exit('summarise: <dir> <reference-arm> <arm>...')
    random.seed(1)
    tags, app = load_q(d)
    ref = arms[0]
    print(f'## Speed against {ref}\n')
    for mode, label in (('hot', 'hot: prepared once, executed repeatedly'), ('prep', 'prep: prepare + bind + step + finalize every execution')):
        S, SE = load(d, ref, mode)
        if not S:
            continue
        print(f'### {label}\n')
        for arm in arms[1:]:
            A, AE = load(d, arm, mode)
            if not A:
                continue
            left, items = [], []
            for i in S:
                if i not in A:
                    left.append((i, 'missing'))
                    continue
                s0 = next(iter(S[i].values()))
                a0 = next(iter(A[i].values()))
                if s0[7] != '-' or a0[7] != '-':
                    left.append((i, 'error: ' + (s0[7] if s0[7] != '-' else a0[7])[:70]))
                    continue
                if (s0[3], s0[4]) != (a0[3], a0[4]) and 'wallclock' not in tags.get(i, []):
                    left.append((i, 'rows differ'))
                    continue
                if min(v[0] for v in S[i].values()) <= 0 or min(v[0] for v in A[i].values()) <= 0:
                    left.append((i, 'timed at 0'))
                    continue
                items.append(i)
            rs = sorted({r for i in items for r in S[i]})
            ra = sorted({r for i in items for r in A[i]})

            def m(X, i, rr, k):
                return statistics.fmean(X[i][r][k] for r in rr if r in X[i])

            ratio, boot = {}, {}
            for k, kn in ((0, 'time'), (1, 'instructions'), (2, 'cycles')):
                if k and any(v[k] < 0 for i in items for X in (S, A) for v in X[i].values()):
                    continue
                ratio[kn] = {i: m(A, i, ra, k) / m(S, i, rs, k) for i in items}
                bs = []

                def mb(X, i, u):
                    own = sorted(X[i])
                    return statistics.fmean(X[i][own[int(x * len(own))]][k] for x in u)

                for _ in range(B if max(len(rs), len(ra)) > 1 else 1):
                    ua = [random.random() for _ in ra]
                    us = [random.random() for _ in rs]
                    bs.append({i: mb(A, i, ua) / mb(S, i, us) for i in items})
                boot[kn] = bs
            kns = list(ratio)

            def cell(kn, its):
                if not its:
                    return '-'
                r = gm([ratio[kn][i] for i in its])
                if len(boot[kn]) == 1:
                    return f'{r:.3f}'
                lo, hi = ci([gm([b[i] for i in its]) for b in boot[kn]])
                return f'{r:.3f} [{lo:.3f}, {hi:.3f}]'

            served = {i: sum(v[5] for v in A[i].values()) / max(1, sum(v[6] for v in A[i].values())) for i in items}
            hdr = '| group | statements | served | ' + ' | '.join(kns) + ' | over 1.02 | under 0.98 |'
            sep = '|---|---:|---:|' + '---:|' * len(kns) + '---:|---:|'

            def row(name, its):
                print(
                    f'| {name} | {len(its)} | {sum(1 for i in its if served[i] > 0.5)} | '
                    + ' | '.join(cell(kn, its) for kn in kns)
                    + f' | {sum(1 for i in its if ratio["time"][i] > 1.02)} | {sum(1 for i in its if ratio["time"][i] < 0.98)} |'
                )

            print(f'#### {arm} / {ref} ({len(items)} statements; rounds {len(rs)} and {len(ra)})\n')
            print(hdr)
            print(sep)
            row('**all**', items)
            if any(served.values()):
                row('served (most executions)', [i for i in items if served[i] > 0.5])
                row('not served', [i for i in items if served[i] <= 0.5])
            for lo, hi, nm in ((0, 0.01, 'reference < 10 µs'), (0.01, 0.1, '10-100 µs'), (0.1, 1, '0.1-1 ms'), (1, 1e9, '≥ 1 ms')):
                its = [i for i in items if lo <= m(S, i, rs, 0) < hi]
                if its:
                    row(nm, its)
            rt = sorted(ratio['time'].values())
            if rt:
                q = lambda p: rt[min(len(rt) - 1, int(p * len(rt)))]
                print(
                    f'\nTime ratio distribution: p5 {q(0.05):.3f}, p25 {q(0.25):.3f}, median {q(0.5):.3f}, p75 {q(0.75):.3f}, p95 {q(0.95):.3f}; '
                    f'min {rt[0]:.3f}, max {rt[-1]:.3f}'
                )
            if AE and SE:  # warm-up vs steady state
                st, wu = {}, []
                for i in items:
                    a_ss, s_ss, nw, wms = [], [], [], 0.0
                    for r in ra:
                        w, s = split(AE.get(i, {}).get(r, []))
                        a_ss += s
                        nw.append(len(w))
                        wms += sum(x[1] for x in w)
                    for r in rs:
                        s_ss += split(SE.get(i, {}).get(r, []))[1]
                    if len(a_ss) >= 3 and len(s_ss) >= 3:
                        st[i] = statistics.fmean(x[1] for x in a_ss) / statistics.fmean(x[1] for x in s_ss)
                    if any(nw):
                        wu.append((i, statistics.fmean(nw), wms / len(ra)))
                if st:
                    print(
                        f'\nSteady state ({len(st)} statements with 3+ steady executions on both sides): time geomean {gm(list(st.values())):.3f}'
                        f'; {sum(1 for v in st.values() if v > 1.02)} over 1.02, {sum(1 for v in st.values() if v < 0.98)} under 0.98'
                    )
                if wu:
                    print(
                        f'Warm-up (executions before the arm\'s last change of served flag): {len(wu)} statements; median '
                        f'{statistics.median(x[1] for x in wu):.0f} executions, {sum(x[2] for x in wu):.1f} ms in all per round'
                    )
            fm = gm([statistics.fmean(A[i][r][8] for r in ra) / statistics.fmean(S[i][r][8] for r in rs) for i in items if all(S[i][r][8] > 0 for r in rs)])
            print(f'\nFirst execution (prepare + first run, untimed in the above): geomean {fm:.3f}\n')
            print('By tag (20 or more statements):\n')
            print(hdr)
            print(sep)
            for t, n in collections.Counter(t for i in items for t in tags.get(i, [])).most_common():
                if n >= 20:
                    row(t, [i for i in items if t in tags[i]])
            print('\nBy application:\n')
            print(hdr)
            print(sep)
            for ap in sorted({app.get(i, '?') for i in items}):
                row(ap, [i for i in items if app.get(i) == ap])

            def one(i):
                return ' | '.join(f'{ratio[kn][i]:.3f}' for kn in kns)

            for title, sel in (
                ('Largest losses', sorted(items, key=lambda i: -ratio['time'][i])[:15]),
                ('Largest wins', sorted(items, key=lambda i: ratio['time'][i])[:15]),
            ):
                print(f'\n{title}:\n')
                print(f'| statement | {ref} ms | served | ' + ' | '.join(kns) + ' |')
                print('|---|---:|---:|' + '---:|' * len(kns))
                for i in sel:
                    print(f'| {i} | {m(S, i, rs, 0):.4f} | {served[i]:.0%} | {one(i)} |')
            if left:
                c = collections.Counter(w.split(':')[0] for _, w in left)
                print(
                    f'\nLeft out ({len(left)}: '
                    + ', '.join(f'{k} {v}' for k, v in c.items())
                    + '): '
                    + '; '.join(f'{i} ({w})' for i, w in left[:40])
                    + (' ...' if len(left) > 40 else '')
                    + '\n'
                )


if __name__ == '__main__':
    main()
