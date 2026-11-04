"""lines: the driver's output records, one per line: a tag, then tab-separated fields. The tags were single letters
before (C N T E R I) and are words now (check norm timing exec rows info), with the same fields in the same order;
split() reads either, so older results still parse. COLUMNS names each record's fields, and header() makes the
'# <tag> <column> ...' lines run.py writes at the top of every .tsv (readers skip lines that start with '#'). An exec
line under SG_EXTRA or SG_SPLIT carries EXEC_MORE's columns after its own (-1 where off or unavailable)."""

TAG = {'C': 'check', 'N': 'norm', 'T': 'timing', 'E': 'exec', 'R': 'rows', 'I': 'info'}
COLUMNS = {
    'check': 'stmt status rows changes hash multiset_hash alt_runs error',
    'norm': 'stmt norm_hash norm_multiset_hash',
    'timing': 'stmt rows hash first_ms median_ms mean_ms runs instructions cycles alt_runs error',
    'exec': 'stmt run alt wall_ms instructions cycles engine_ms',
    'rows': 'stmt [values...]',
    'info': 'backend dialect version',
}


EXEC_MORE = 'kernel_cycles branch_misses page_faults context_switches step_ms column_ms reset_ms'


def split(line):
    """a line's fields, its tag as a word whichever form it was written in"""
    p = line.rstrip('\n').split('\t')
    p[0] = TAG.get(p[0], p[0])
    return p


def header(*tags, exec_more=False):
    """the '#' header lines for these record types (exec_more: exec lines with EXEC_MORE's columns)"""
    cols = {t: COLUMNS[t] + (' ' + EXEC_MORE if t == 'exec' and exec_more else '') for t in tags}
    return ''.join('# ' + '\t'.join([t] + cols[t].split()) + '\n' for t in tags)
