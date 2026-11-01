"""compare: the arms' <arm>.check.tsv side by side, statement by statement, against the first arm (the reference).
For each arm and statement, one verdict, the first that applies:
  same          status, row count, changes and error agree, and the rows hash the same: in order when the statement
                has ORDER BY, as a multiset otherwise (statements tagged wallclock read the clock: row count only)
  types         the rows agree once normalised (numbers as one kind, floats to 12 significant digits) but not
                byte for byte: an affinity or integer/real difference
  order         an ORDER BY statement whose rows agree as a multiset but not in order: ties or NULL placement
  rows          the same number of rows, different values
  rowcount      a different number of rows
  changes       a write changed a different number of rows
  error         only this arm failed;  ref-error: only the reference failed;  both-error: both failed (messages
                may differ, listed)
  unstable      the arm disagreed with itself across its 8 runs
  missing       no line for it (the process died: see the run's stderr)
  untranslated  no text for the arm's dialect (prep's <p>.untranslated.tsv)
The report: verdict counts per arm, the error messages by frequency, every statement where some arm is not "same",
with the arms grouped by which ones agree with each other. Writes <dir>/disagreements.tsv."""

import collections
import glob
import os
import re
import sys

ORDER = ('same', 'types', 'order', 'rows', 'rowcount', 'changes', 'error', 'ref-error', 'both-error', 'unstable', 'missing', 'untranslated')


def load_q(d):
    ordered, tags, app = {}, {}, {}
    for f in glob.glob(os.path.join(d, 'q', '*.q')):
        for l in open(f):
            if l.startswith('@@Q '):
                p = l.split()
                ordered[p[1]] = p[2] == '1'
                tags[p[1]] = p[4].split(',')
                app[p[1]] = os.path.basename(f)[:-2]
    untr = {}
    for f in glob.glob(os.path.join(d, 'q-*', '*.untranslated.tsv')):  # (another dialect's: run.py's q-<dialect>)
        for l in open(f):
            i, _, w = l.rstrip('\n').partition('\t')
            untr[i] = w
    return ordered, tags, app, untr


def load(d, arm):
    r = {}
    for l in open(os.path.join(d, f'{arm}.check.tsv'), errors='replace'):
        p = l.rstrip('\n').split('\t')
        if p[0] == 'C' and len(p) >= 9:
            r[p[1]] = dict(st=p[2], rows=p[3], ch=p[4], h=p[5], hs=p[6], alt=p[7], err=p[8])
        elif p[0] == 'N' and len(p) >= 4 and p[1] in r:
            r[p[1]].update(nh=p[2], nhs=p[3])
    return r


def verdict(s, a, ordered, wall, read=False):
    if a is None:
        return 'missing'
    if a['st'] == 'unstable':
        return 'unstable'
    se, ae = s['st'] != 'ok', a['st'] != 'ok'
    if se and ae:
        return 'both-error'
    if ae:
        return 'error'
    if se:
        return 'ref-error'
    if a['rows'] != s['rows']:
        return 'rowcount'
    if not read and a['ch'] != s['ch']:  # (after a read, an engine may still report its last write's count)
        return 'changes'
    if wall:
        return 'same'
    if (a['h'] if ordered else a['hs']) == (s['h'] if ordered else s['hs']):
        return 'same'
    norm = 'nh' in a and 'nh' in s
    if norm and (a['nh'] if ordered else a['nhs']) == (s['nh'] if ordered else s['nhs']):
        return 'types'
    if ordered and (a['hs'] == s['hs'] or (norm and a['nhs'] == s['nhs'])):
        return 'order'
    return 'rows'


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    d = argv[0]
    arms = argv[1:] or sorted(os.path.basename(f)[:-10] for f in glob.glob(os.path.join(d, '*.check.tsv')))
    ordered, tags, app, untr = load_q(d)
    R = {a: load(d, a) for a in arms}
    ref = arms[0]
    S = R[ref]
    ids = sorted(set(S) | set(untr))
    V = {a: {} for a in arms[1:]}
    for a in arms[1:]:
        for i in ids:
            if i in untr and i not in R[a]:
                V[a][i] = 'untranslated'
                continue
            if i not in S:
                continue
            V[a][i] = verdict(S[i], R[a].get(i), ordered.get(i, False), 'wallclock' in tags.get(i, []), 'read' in tags.get(i, []))
    print(f'## Correctness: {len(S)} statements, reference {ref}\n')
    bad = [i for i, x in S.items() if x['st'] != 'ok']
    print(
        f'{ref}: {len(S) - len(bad)} ok, {sum(x["st"] == "err" for x in S.values())} errors, '
        f'{sum(x["st"] == "unstable" for x in S.values())} unstable (runs disagree with each other)\n'
    )
    exp = {}  # (prep's <p>.expected.tsv: the manifest's row and change counts, recorded with the sqlite3 CLI at scale 1;
    #  changes only for writes: after a read, an engine may still report the last write's count)
    for f in glob.glob(os.path.join(d, 'q', '*.expected.tsv')):
        for l in open(f):
            i, r, c = l.rstrip('\n').split('\t')
            exp[i] = (r, c)
    if exp:
        for a in arms:
            off = [
                i
                for i, x in R[a].items()
                if i in exp
                and x['st'] == 'ok'
                and 'wallclock' not in tags.get(i, [])
                and (x['rows'] != exp[i][0] or ('read' not in tags.get(i, []) and x['ch'] != exp[i][1]))
            ]
            print(
                f'{a} against the manifest\'s counts: {sum(1 for i in R[a] if i in exp) - len(off)} agree, {len(off)} differ'
                + (': ' + ', '.join(f'{i} ({R[a][i]["rows"]} rows, manifest {exp[i][0]})' for i in off[:12]) if off else '')
            )
        print()
    print('| arm | ' + ' | '.join(ORDER) + ' |')
    print('|---|' + '---:|' * len(ORDER))
    for a in arms[1:]:
        c = collections.Counter(V[a].values())
        print(f'| {a} | ' + ' | '.join(str(c.get(k, 0) or '') for k in ORDER) + ' |')
    print()
    for a in arms:
        msgs = collections.Counter()
        for x in R[a].values():
            if x['st'] != 'ok':
                m = re.split(r'\s+LINE \d+:|\s+DETAIL:', x['err'].split(' [run')[0])[0]  # (the message, not where it struck)
                for pre in ('prepare: ',):
                    m = m[len(pre) :] if m.startswith(pre) else m
                msgs[m[:90]] += 1
        if msgs:
            print(f'{a} errors by message: ' + '; '.join(f'{m} ({n})' for m, n in msgs.most_common(12)) + '\n')
    rows = []
    for i in ids:
        vs = {a: V[a].get(i) for a in arms[1:]}
        if all(v in ('same', None) for v in vs.values()):
            continue
        groups = collections.defaultdict(list)  # (arms whose results equal each other's, normalised as a multiset)
        for a in arms:
            x = R[a].get(i)
            key = (
                'untranslated'
                if x is None and i in untr
                else 'missing'
                if x is None
                else (x['st'], x['rows'], x['ch'], x.get('nhs', x['hs']))
                if x['st'] == 'ok'
                else ('err',)
            )
            groups[key].append(a)
        part = ' | '.join('{' + ','.join(g) + '}' for g in groups.values())
        why = []
        for a in arms:
            x = R[a].get(i)
            if x and x['st'] != 'ok':
                why.append(f'{a}: {x["err"][:100]}')
            elif x and S.get(i) and x['rows'] != S[i]['rows']:
                why.append(f'{a}: {x["rows"]} rows')
        rows.append((i, vs, part, '; '.join(why)))
    print(f'### Statements where an arm differs from {ref} ({len(rows)})\n')
    print('| statement | ' + ' | '.join(arms[1:]) + ' | agreeing groups | detail |')
    print('|---|' + '---|' * (len(arms) + 1))
    for i, vs, part, why in rows:
        print(f'| {i} | ' + ' | '.join(vs[a] or '-' for a in arms[1:]) + f' | {part} | {why.replace("|", "/")} |')
    with open(os.path.join(d, 'disagreements.tsv'), 'w') as f:
        f.write('# id\t' + '\t'.join(arms[1:]) + '\tgroups\tdetail\n')
        for i, vs, part, why in rows:
            f.write(f'{i}\t' + '\t'.join(vs[a] or '-' for a in arms[1:]) + f'\t{part}\t{why}\n')
    for a in arms[1:]:
        sv = [x['alt'] for x in R[a].values() if '/' in x['alt']]
        full = sum(1 for s in sv if s.split('/')[0] == s.split('/')[1] and s != '0/0')
        some = sum(1 for s in sv if 0 < int(s.split('/')[0]) < int(s.split('/')[1]))
        if full or some:
            print(f'\n{a} on its alternative path (every execution / some): {full} / {some} statements')
    return 1 if any(v not in ('same',) for a in V for v in V[a].values()) else 0


if __name__ == '__main__':
    sys.exit(main())
