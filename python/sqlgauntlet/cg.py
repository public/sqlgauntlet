"""cg: the driver's T lines with the instructions per execution taken from callgrind's dumps (the driver built with
-DSG_CALLGRIND, make callgrind: one dump per statement and mode, labelled "<id> <mode> <executions>", its Ir the counted
regions only), and cycles -1; the rest as the driver printed it. A statement without a dump keeps -1."""

VALGRIND = ['valgrind', '--tool=callgrind', '--collect-atstart=no', '--dump-line=no']


def totals(dumps):
    """{id: (instructions, executions)} from callgrind.out files"""
    ir = {}
    for f in dumps:
        label = n = None
        for line in open(f, errors='replace'):
            if line.startswith('desc: Trigger: Client Request: '):
                label = line[len('desc: Trigger: Client Request: ') :].strip()
            elif line.startswith(('summary:', 'totals:')):
                n = int(line.split()[1])
        if label and n is not None:
            i, _, k = label.rsplit(' ', 2)
            ir[i] = (n, int(k))
    return ir


def apply(lines, dumps):
    """the T lines of lines (an iterable of output lines) with callgrind's counts"""
    ir = totals(dumps)
    for line in lines:
        p = line.rstrip('\n').split('\t')
        if p[0] == 'T' and len(p) >= 12:
            n, k = ir.get(p[1], (None, 0))
            p[8] = f'{n / k:.1f}' if n is not None and k else '-1'
            p[9] = '-1'
        yield '\t'.join(p) + '\n'
