/* driver.c: libsqlgauntlet's driver, sg_main(). It reads a corpus file (python/sqlgauntlet/prep.py's <project>.q) and runs
** its statements through a backend table (include/sqlgauntlet.h). Modes, argv[1]:
**   build <db> <project.build.sql>                make the database (a script through the backend's exec)
**   load <db> <table> <file> <tbl|csv>            a data file into a table (load() below; workloads' load.tsv)
**   check <db> <project.q> [only-id]              each statement 6 times on one handle, then twice freshly prepared:
**        (a statement tagged wallclock agrees with itself by row count: its values follow the clock)
**        C id status rows changes hash msethash alt/runs error      status ok | err | unstable (runs disagree)
**        N id nhash nmsethash                      the same rows normalised (below), for comparing different engines
**   hot|prep <db> <project.q> [reps] [budget-ms] [only-id]   timing:
**        T id rows hash first-ms median-ms mean-ms runs instr cycles alt/runs error
**     hot: prepared once, then 1 + reps executions (reset between); prep: every execution prepares, binds, steps and
**     finalizes. reps stop early once budget-ms is spent (at least SG_MIN_REPS, default 3). instr, cycles: user mode,
**     per execution (Linux perf_event_open; -1 elsewhere or when the kernel refuses).
**     SG_EXEC=1: after each T line, one E line per counted execution, in order (k from 1), for warm-up vs steady state:
**        E id k alt ms instr cycles engine-ms
**   rows <db> <project.q> <id> [limit]            the statement's normalised rows, one R line each (disagreement reports)
**   info                                          the backend's name, dialect and version: I name dialect version
** hash and msethash are FNV-1a over each value's kind and bytes. alt/runs: of the runs, how many the backend reported on
** its alternative path (sg_exec_stats.alt_path; 0 for a backend without one).
** Reads run in autocommit; writes inside BEGIN ... ROLLBACK, outside the timed span. hash: the rows in order;
** msethash: the rows as a multiset.
** Normalised values (N lines, rows): integers and floats are one kind, numbers; a float with an exact integer value
** within 2^53 hashes as that integer, any other float as its value rounded to SG_DIGITS significant digits (default
** 12); text and blobs keep their bytes and their kind; NULL is NULL. So 3 and 3.0 agree, 0.1+0.2 and 0.3 agree, '3' and
** 3 do not. */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <math.h>
#include <time.h>
#include <unistd.h>
#include "sqlgauntlet.h"
#ifdef SG_CALLGRIND /* (make callgrind: build/callgrind/sqlgauntlet, the counters are valgrind's) */
#include <valgrind/callgrind.h>
#endif
#ifdef __linux__
#include <linux/perf_event.h>
#include <sys/ioctl.h>
#include <sys/syscall.h>
#endif

static const sg_backend *B;
static sg_conn *db;
static char err[512];

static double now_ms(void) {
  struct timespec t;
  clock_gettime(CLOCK_MONOTONIC, &t);
  return t.tv_sec * 1e3 + t.tv_nsec / 1e6;
}
static unsigned long long fnv(unsigned long long h, const void *p, size_t n) {
  const unsigned char *c = p;
  for (size_t i = 0; i < n; i++) h = (h ^ c[i]) * 1099511628211ULL;
  return h;
}
static int cmpd(const void *a, const void *b) {
  double x = *(const double *)a, y = *(const double *)b;
  return x < y ? -1 : x > y;
}

/* user-mode instructions and cycles over the timed spans. Built with -DSG_CALLGRIND (make callgrind) the counted
** regions are callgrind's instead: collection toggled on and off around them, zeroed per statement, and dumped with the
** label "<id> <hot|prep> <executions>" (python/sqlgauntlet/cg.py puts the counts into the T lines): deterministic
** instruction counts on any Linux, for statements too short to time. */
static int perf_fd[2] = { -1, -1 };
static void perf_open(void) {
#ifdef __linux__
  static const unsigned long long cfg[2] = { PERF_COUNT_HW_INSTRUCTIONS, PERF_COUNT_HW_CPU_CYCLES };
  for (int i = 0; i < 2; i++) {
    struct perf_event_attr a;
    memset(&a, 0, sizeof a);
    a.size = sizeof a;
    a.type = PERF_TYPE_HARDWARE;
    a.config = cfg[i];
    a.disabled = 1;
    a.exclude_kernel = 1;
    a.exclude_hv = 1;
    perf_fd[i] = (int)syscall(SYS_perf_event_open, &a, 0, -1, -1, 0);
    if (perf_fd[i] < 0) {
      fprintf(stderr, "sqlgauntlet: perf_event_open: counters unavailable\n");
      perf_fd[0] = perf_fd[1] = -1;
      return;
    }
  }
#endif
}
static void perf_on(int on) {
#ifdef SG_CALLGRIND
  static int cg;
  if (on != cg) {
    CALLGRIND_TOGGLE_COLLECT;
    cg = on;
  }
#elif defined(__linux__)
  for (int i = 0; i < 2; i++)
    if (perf_fd[i] >= 0) ioctl(perf_fd[i], on ? PERF_EVENT_IOC_ENABLE : PERF_EVENT_IOC_DISABLE, 0);
#else
  (void)on;
#endif
}
static long long perf_read(int i) {
  long long v = -1;
  if (perf_fd[i] >= 0 && read(perf_fd[i], &v, sizeof v) != sizeof v) v = -1;
  return v;
}
static void perf_reset(void) {
#ifdef SG_CALLGRIND
  CALLGRIND_ZERO_STATS;
#elif defined(__linux__)
  for (int i = 0; i < 2; i++)
    if (perf_fd[i] >= 0) ioctl(perf_fd[i], PERF_EVENT_IOC_RESET, 0);
#endif
}

typedef struct Q {
  char *id;
  int ordered, write;
  char *tags;
  int np, cp;
  char **pn, **pl;
  char *sql;
} Q;
typedef struct R {
  long rows, changes;
  unsigned long long h, hs, nh, nhs;
  int alt;
  double engine_ms;
  char err[256];
} R;

static sg_stmt *s_begin, *s_rollback;
typedef struct {
  sg_value v;
  void *own;
} PV;
static PV *pv;
static int npv; /* the current statement's parameter values (each literal evaluated once, by the backend) */

static char *slurp(const char *f) {
  FILE *fp = fopen(f, "rb");
  if (!fp) {
    perror(f);
    exit(2);
  }
  fseek(fp, 0, SEEK_END);
  long n = ftell(fp);
  fseek(fp, 0, SEEK_SET);
  char *b = malloc((size_t)n + 1);
  if (fread(b, 1, (size_t)n, fp) != (size_t)n) {
    perror(f);
    exit(2);
  }
  b[n] = 0;
  fclose(fp);
  return b;
}
static char *pragma[16];
static int npragma; /* the project's @@X lines: connection settings, run before the statements */
static int parse(char *b, Q **out) {
  int n = 0, cap = 64;
  Q *q = malloc(sizeof *q * cap);
  char *p = b;
  while (*p) {
    char *nl = strchr(p, '\n');
    if (nl) *nl = 0;
    if (!strncmp(p, "@@Q ", 4)) {
      if (n == cap) q = realloc(q, sizeof *q * (cap *= 2));
      Q *x = &q[n++];
      memset(x, 0, sizeof *x);
      x->id = strtok(p + 4, " ");
      x->ordered = atoi(strtok(0, " "));
      x->write = atoi(strtok(0, " "));
      x->tags = strtok(0, " ");
    } else if (!strncmp(p, "@@X ", 4) && !n) {
      if (npragma < 16) pragma[npragma++] = p + 4;
    } else if (!strncmp(p, "@@P ", 4) && n) {
      Q *x = &q[n - 1];
      char *sp = strchr(p + 4, ' ');
      if (sp) {
        if (x->np == x->cp) {
          x->cp = x->cp ? 2 * x->cp : 8;
          x->pn = realloc(x->pn, sizeof *x->pn * x->cp);
          x->pl = realloc(x->pl, sizeof *x->pl * x->cp);
        }
        *sp = 0;
        x->pn[x->np] = p + 4;
        x->pl[x->np++] = sp + 1;
      }
    } else if (n) {
      Q *x = &q[n - 1];
      if (!x->sql) x->sql = p;
      else p[-1] = '\n'; /* (rejoin the SQL lines) */
    }
    if (!nl) break;
    p = nl + 1;
  }
  *out = q;
  return n;
}

/* parameter literals: "SELECT <literal>" through the backend, the value copied (so every engine binds its own reading
** of the literal, as the application's driver would hand it over) */
static void params_eval(Q *q) {
  if (q->np > npv) {
    pv = realloc(pv, sizeof *pv * q->np);
    memset(pv + npv, 0, sizeof *pv * (q->np - npv));
    npv = q->np;
  }
  for (int i = 0; i < q->np; i++) {
    free(pv[i].own);
    memset(&pv[i], 0, sizeof pv[i]);
    size_t n = strlen(q->pl[i]) + 8;
    char *s = malloc(n);
    snprintf(s, n, "SELECT %s", q->pl[i]);
    sg_stmt *st = B->prepare(db, s, err, sizeof err);
    if (st && B->step(st) == SG_ROW) {
      sg_value v;
      B->column(st, 0, &v);
      pv[i].v = v;
      if (v.type == SG_TEXT || v.type == SG_BLOB) {
        pv[i].own = malloc(v.n + 1);
        if (v.n) memcpy(pv[i].own, v.p, v.n);
        ((char *)pv[i].own)[v.n] = 0;
        pv[i].v.p = pv[i].own;
      }
    } else pv[i].v.type = 0; /* (left unbound: the engine cannot evaluate the literal) */
    if (st) B->finalize(st);
    free(s);
  }
}
static void bind(Q *q, sg_stmt *st) {
  for (int i = 0; i < q->np; i++) {
    int k = B->param_index(st, q->pn[i]); /* (an anonymous ? has no name: "?N" is the Nth) */
    if (k == 0 && q->pn[i][0] == '?') {
      k = atoi(q->pn[i] + 1);
      if (k > B->param_count(st)) k = 0;
    }
    if (k > 0 && pv[i].v.type) B->bind(st, k, &pv[i].v);
  }
}

static int digits = 12;
static unsigned long long nval(unsigned long long h, const sg_value *v) { /* a normalised value into h */
  int kind = v->type == SG_FLOAT ? SG_INTEGER : v->type;
  h = fnv(h, &kind, sizeof kind);
  if (v->type == SG_INTEGER) {
    double d = (double)v->i;
    if (d >= -9007199254740992.0 && d <= 9007199254740992.0) {
      long long x = v->i;
      h = fnv(h, &x, sizeof x);
    } else {
      char b[40];
      int n = snprintf(b, sizeof b, "%.*g", digits, d);
      h = fnv(h, b, (size_t)n);
    }
  } else if (v->type == SG_FLOAT) {
    double d = v->f;
    if (d == floor(d) && fabs(d) <= 9007199254740992.0) {
      long long x = (long long)d;
      h = fnv(h, &x, sizeof x);
    } else {
      char b[40];
      int n = snprintf(b, sizeof b, "%.*g", digits, d);
      h = fnv(h, b, (size_t)n);
    }
  } else if (v->type == SG_TEXT || v->type == SG_BLOB) h = fnv(h, v->p, v->n);
  return h;
}
static void ntext(const sg_value *v, char *b, size_t n) { /* a normalised value as text (rows mode) */
  if (v->type == SG_NULL) snprintf(b, n, "NULL");
  else if (v->type == SG_INTEGER) snprintf(b, n, "%lld", (long long)v->i);
  else if (v->type == SG_FLOAT) {
    if (v->f == floor(v->f) && fabs(v->f) <= 9007199254740992.0) snprintf(b, n, "%lld", (long long)v->f);
    else snprintf(b, n, "%.*g", digits, v->f);
  } else if (v->type == SG_TEXT) {
    size_t k = v->n < n - 3 ? v->n : n - 3;
    b[0] = '\'';
    memcpy(b + 1, v->p, k);
    b[k + 1] = '\'';
    b[k + 2] = 0;
    for (size_t i = 1; i <= k; i++)
      if (b[i] == '\t' || b[i] == '\n') b[i] = ' ';
  } else {
    size_t o = (size_t)snprintf(b, n, "x'");
    for (size_t i = 0; i < v->n && o + 4 < n; i++) o += (size_t)snprintf(b + o, n - o, "%02x", ((const unsigned char *)v->p)[i]);
    snprintf(b + o, n - o, "'");
  }
}

/* one execution of a prepared statement (timed: steps and row reads only); writes inside BEGIN ... ROLLBACK */
static double t_exec;
static int show, rows_limit = -1;
static int exec1(Q *q, sg_stmt *st, R *r) {
  if (q->write) {
    B->step(s_begin);
    B->reset(s_begin);
  }
  if (B->exec_begin) B->exec_begin(st);
  unsigned long long h = 1469598103934665603ULL, hs = 0, nh = 1469598103934665603ULL, nhs = 0;
  long n = 0;
  int rc;
  perf_on(1);
  double t0 = now_ms();
  while ((rc = B->step(st)) == SG_ROW) {
    unsigned long long rh = 1469598103934665603ULL, nrh = 1469598103934665603ULL;
    int nc = B->column_count(st);
    for (int i = 0; i < nc; i++) {
      sg_value v;
      B->column(st, i, &v);
      int t = v.type;
      rh = fnv(rh, &t, sizeof t);
      if (t == SG_INTEGER) rh = fnv(rh, &v.i, sizeof v.i);
      else if (t == SG_FLOAT) rh = fnv(rh, &v.f, sizeof v.f);
      else if (t == SG_TEXT || t == SG_BLOB) rh = fnv(rh, v.p, v.n);
      nrh = nval(nrh, &v);
      if (show || (rows_limit >= 0 && n < rows_limit)) {
        char b[256];
        ntext(&v, b, sizeof b);
        if (show) fprintf(stderr, "%s%s", i ? "|" : "ROW ", b);
        else printf("%s%s", i ? "\t" : "", b);
      }
    }
    if (show) fputc('\n', stderr);
    else if (rows_limit >= 0 && n < rows_limit) {
      if (!nc) printf("(no columns)");
      putchar('\n');
    }
    h = fnv(h, &rh, sizeof rh);
    hs += rh * 0x9E3779B97F4A7C15ULL + (rh >> 29);
    nh = fnv(nh, &nrh, sizeof nrh);
    nhs += nrh * 0x9E3779B97F4A7C15ULL + (nrh >> 29);
    n++;
  }
  t_exec = now_ms() - t0;
  perf_on(0);
  r->rows = n;
  r->h = h;
  r->hs = hs;
  r->nh = nh;
  r->nhs = nhs;
  r->changes = (long)B->changes(db);
  sg_exec_stats es = { -1, -1, { -1, -1, -1, -1 } };
  if (B->exec_stats) B->exec_stats(st, &es);
  r->alt = es.alt_path == 1 && rc == SG_DONE;
  r->engine_ms = es.engine_ms;
  r->err[0] = 0;
  if (rc != SG_DONE) snprintf(r->err, sizeof r->err, "%s", B->errmsg(db));
  B->reset(st);
  if (q->write) {
    B->step(s_rollback);
    B->reset(s_rollback);
  }
  return rc == SG_DONE ? 0 : 1;
}
static int prep(Q *q, sg_stmt **st, R *r) {
  if (!(*st = B->prepare(db, q->sql, err, sizeof err))) {
    snprintf(r->err, sizeof r->err, "prepare: %s", err);
    return 1;
  }
  bind(q, *st);
  return 0;
}
/* load <db> <table> <file> <tbl|csv>: a data file into an existing table through the backend's own prepare, bind and
** step, inside one transaction. Every field is bound as text, so the column's declared type converts it (as the sqlite3
** shell's .import does, and dbgen's loaders). tbl: '|'-separated, with or without a trailing '|' (TPC-H's dbgen). csv:
** RFC 4180, ',' separated, a quoted field may hold '"' (as "") and newlines. csvn: csv where an unquoted \N is NULL
** (PostgreSQL's convention; export.py writes it). tsv: tab-separated, every field verbatim (no quoting), as the
** sqlite3 shell's ascii mode imports it. A line with the wrong field count stops it. */
static int rec(FILE *f, int csv, int sep, char **buf, size_t *cap, size_t *off, unsigned char *qf, int nf) { /* one record's fields into buf */
  size_t n = 0;
  int c, inq = 0, k = 0, any = 0;
  off[0] = 0;
  qf[0] = 0; /* (qf[k]: field k was quoted) */
  while ((c = getc(f)) != EOF) {
    any = 1;
    if (n + 2 >= *cap) *buf = realloc(*buf, *cap *= 2);
    if (csv && inq) {
      if (c == '"') {
        int d = getc(f);
        if (d == '"') (*buf)[n++] = '"';
        else {
          inq = 0;
          if (d != EOF) ungetc(d, f);
        }
      } else (*buf)[n++] = (char)c;
      continue;
    }
    if (csv && c == '"') {
      inq = 1;
      if (k < nf + 1) qf[k] = 1;
      continue;
    }
    if (c == '\r') continue;
    if (c == '\n') break;
    if (c == sep) {
      (*buf)[n++] = 0;
      if (++k < nf + 1) {
        off[k] = n;
        qf[k] = 0;
      }
      continue;
    }
    (*buf)[n++] = (char)c;
  }
  if (!any) return -1;
  (*buf)[n] = 0;
  if (sep == '|' && n && off[k] == n && k == nf) return k; /* (a trailing '|': its empty last field isn't a field) */
  return k + 1;
}
static int load(const char *table, const char *file, const char *fmt) {
  int csvn = !strcmp(fmt, "csvn"), csv = csvn || !strcmp(fmt, "csv"), tsv = !strcmp(fmt, "tsv"), sep = csv ? ',' : tsv ? '\t' : '|';
  if (!csv && !tsv && strcmp(fmt, "tbl")) {
    fprintf(stderr, "sqlgauntlet: load: format %s: tbl, tsv, csv or csvn\n", fmt);
    return 2;
  }
  char sql[1024];
  snprintf(sql, sizeof sql, "SELECT * FROM %s", table);
  sg_stmt *st = B->prepare(db, sql, err, sizeof err);
  if (!st) {
    fprintf(stderr, "sqlgauntlet: load: %s: %s\n", table, err);
    return 1;
  }
  int nc = B->column_count(st);
  B->finalize(st);
  int dollar = B->dialect && !strcmp(B->dialect, "postgres"); /* (the dialect's placeholders: $1.. or ?) */
  size_t len = (size_t)snprintf(sql, sizeof sql, "INSERT INTO %s VALUES (", table);
  for (int i = 1; i <= nc && len < sizeof sql - 12; i++)
    len += dollar ? (size_t)snprintf(sql + len, sizeof sql - len, "%s$%d", i > 1 ? ", " : "", i)
                  : (size_t)snprintf(sql + len, sizeof sql - len, "%s?", i > 1 ? ", " : "");
  snprintf(sql + len, sizeof sql - len, ")");
  sg_stmt *ins = B->prepare(db, sql, err, sizeof err);
  FILE *f = fopen(file, "rb");
  if (!ins || !f) {
    fprintf(stderr, "sqlgauntlet: load: %s: %s\n", ins ? file : sql, ins ? "cannot open" : err);
    return 1;
  }
  size_t cap = 1 << 16, *off = malloc(sizeof *off * (size_t)(nc + 2));
  unsigned char *qf = malloc((size_t)nc + 2);
  char *buf = malloc(cap);
  long rows = 0;
  int k, rc = 0;
  if (B->exec(db, "BEGIN", err, sizeof err)) fprintf(stderr, "sqlgauntlet: load: BEGIN: %s\n", err);
  while ((k = rec(f, csv, sep, &buf, &cap, off, qf, nc)) >= 0) {
    if (k == 1 && !buf[0]) continue; /* (an empty line) */
    if (k != nc) {
      fprintf(stderr, "sqlgauntlet: load: %s: record %ld has %d fields, %s has %d columns\n", file, rows + 1, k, table, nc);
      rc = 1;
      break;
    }
    for (int i = 0; i < nc; i++) {
      sg_value v = { SG_TEXT, 0, 0, buf + off[i], strlen(buf + off[i]) };
      if (csvn && !qf[i] && !strcmp(buf + off[i], "\\N")) v.type = SG_NULL;
      B->bind(ins, i + 1, &v);
    }
    if (B->step(ins) != SG_DONE) {
      fprintf(stderr, "sqlgauntlet: load: %s: record %ld: %s\n", file, rows + 1, B->errmsg(db));
      rc = 1;
      break;
    }
    B->reset(ins);
    rows++;
  }
  if (B->exec(db, rc ? "ROLLBACK" : "COMMIT", err, sizeof err)) fprintf(stderr, "sqlgauntlet: load: COMMIT: %s\n", err);
  B->finalize(ins);
  fclose(f);
  free(buf);
  free(off);
  free(qf);
  fprintf(stderr, "sqlgauntlet: load: %s: %ld rows\n", table, rows);
  return rc;
}
static int tagged(const Q *q, const char *tag) { /* tag is one of the statement's comma-separated tags */
  size_t n = strlen(tag);
  for (const char *t = q->tags; t && (t = strstr(t, tag)); t += n)
    if ((t == q->tags || t[-1] == ',') && (t[n] == ',' || !t[n])) return 1;
  return 0;
}
static void clean(char *s) {
  for (; *s; s++)
    if (*s == '\t' || *s == '\n') *s = ' ';
}

int sg_main(const sg_backend *b, int argc, char **argv) {
  B = b;
  if (!B || B->abi != SQLGAUNTLET_ABI) {
    fprintf(stderr, "sqlgauntlet: backend ABI %d, driver %d\n", B ? B->abi : -1, SQLGAUNTLET_ABI);
    return 2;
  }
  if (argc >= 2 && !strcmp(argv[1], "info")) {
    printf("I\t%s\t%s\t%s\n", B->name, B->dialect, B->version ? B->version() : "-");
    return 0;
  }
  if (argc < 4) {
    fprintf(stderr, "usage: sqlgauntlet build|load|check|hot|prep|rows|info <db> <file> ...\n");
    return 2;
  }
  const char *mode = argv[1];
  show = getenv("SG_ROWS") != 0;
  int sexec = getenv("SG_EXEC") && atoi(getenv("SG_EXEC")) > 0;
  if (getenv("SG_DIGITS")) digits = atoi(getenv("SG_DIGITS"));
  if (!(db = B->open(argv[2], err, sizeof err))) {
    fprintf(stderr, "sqlgauntlet: open %s: %s\n", argv[2], err);
    return 2;
  }
  if (!strcmp(mode, "load")) {
    if (argc < 6) {
      fprintf(stderr, "usage: sqlgauntlet load <db> <table> <file> <tbl|csv>\n");
      return 2;
    }
    int rc = load(argv[3], argv[4], argv[5]);
    B->close(db);
    return rc;
  }
  if (!strcmp(mode, "build")) {
    char *sql = slurp(argv[3]);
    if (B->exec(db, sql, err, sizeof err)) {
      fprintf(stderr, "sqlgauntlet: build %s: %s\n", argv[3], err);
      return 1;
    }
    B->close(db);
    return 0;
  }
  s_begin = B->prepare(db, "BEGIN", err, sizeof err);
  s_rollback = B->prepare(db, "ROLLBACK", err, sizeof err);
  Q *qs;
  int nq = parse(slurp(argv[3]), &qs);
  for (int i = 0; i < npragma; i++)
    if (B->exec(db, pragma[i], err, sizeof err)) {
      fprintf(stderr, "sqlgauntlet: %s: %s\n", pragma[i], err);
      return 1;
    }
  int check = !strcmp(mode, "check"), hot = !strcmp(mode, "hot"), rows = !strcmp(mode, "rows");
  if (!check && !hot && !rows && strcmp(mode, "prep")) {
    fprintf(stderr, "sqlgauntlet: unknown mode %s\n", mode);
    return 2;
  }
  int reps = argc > 4 && !check && !rows ? atoi(argv[4]) : 200;
  double budget = argc > 5 && !check && !rows ? atof(argv[5]) : 100;
  const char *only = check ? (argc > 4 ? argv[4] : 0) : rows ? argv[4] : (argc > 6 ? argv[6] : 0);
  int minr = getenv("SG_MIN_REPS") ? atoi(getenv("SG_MIN_REPS")) : 3;
  if (!check && !rows) perf_open();
  double *t = malloc(sizeof *t * (size_t)(reps + 1));
  struct E {
    int alt;
    double ms, ins, cyc, eng;
  } *ex = sexec ? malloc(sizeof *ex * (size_t)(reps + 1)) : 0;
  for (int i = 0; i < nq; i++) {
    Q *q = &qs[i];
    if (only && strcmp(only, q->id)) continue;
    params_eval(q);
    R r0, r;
    memset(&r0, 0, sizeof r0);
    memset(&r, 0, sizeof r);
    sg_stmt *st = 0;
    if (rows) {
      rows_limit = argc > 5 ? atoi(argv[5]) : 20;
      printf("R\t%s\n", q->id);
      fflush(stdout);
      if (!prep(q, &st, &r0)) {
        exec1(q, st, &r0);
        B->finalize(st);
      }
      printf("R\t%s\t%ld rows\t%s\n", q->id, r0.rows, r0.err[0] ? r0.err : "-");
      rows_limit = -1;
      continue;
    }
    if (check) {
      const char *status = "ok";
      int alt = 0, runs = 0, wall = tagged(q, "wallclock"); /* (reads the clock: its runs agree by row count, as compare holds it) */
      if (prep(q, &st, &r0)) status = "err";
      else {
        for (int k = 0; k < 8; k++) { /* 6 runs on one handle, then 2 on fresh handles */
          if (k >= 6) {
            B->finalize(st);
            st = 0;
            if (prep(q, &st, &r)) {
              status = "unstable";
              break;
            }
          }
          R *x = k ? &r : &r0;
          exec1(q, st, x);
          alt += x->alt;
          runs++;
          if (k && (r.rows != r0.rows || (!wall && (q->ordered ? r.h != r0.h : r.hs != r0.hs)) || r.changes != r0.changes || strcmp(r.err, r0.err))) {
            status = "unstable";
            snprintf(r0.err + strlen(r0.err), sizeof r0.err - strlen(r0.err), " [run %d: %ld rows %016llx %s]", k, r.rows, r.h, r.err);
            break;
          }
        }
        if (!strcmp(status, "ok") && r0.err[0]) status = "err";
      }
      if (st) B->finalize(st);
      clean(r0.err);
      printf("C\t%s\t%s\t%ld\t%ld\t%016llx\t%016llx\t%d/%d\t%s\n", q->id, status, r0.rows, r0.changes, r0.h, r0.hs, alt, runs, r0.err[0] ? r0.err : "-");
      printf("N\t%s\t%016llx\t%016llx\n", q->id, r0.nh, r0.nhs);
    } else {
      int nt = 0, alt = 0;
      double spent = 0, first;
      if (hot) {
        if (prep(q, &st, &r0)) goto bad;
        exec1(q, st, &r0);
        first = t_exec;
        perf_reset();
        for (int k = 0; k < reps && (k < minr || spent < budget) && !r0.err[0]; k++) {
          long long i0 = ex ? perf_read(0) : 0, c0 = ex ? perf_read(1) : 0;
          exec1(q, st, &r);
          t[nt] = t_exec;
          spent += t_exec;
          alt += r.alt;
          if (ex)
            ex[nt] =
              (struct E){ r.alt, t_exec, perf_fd[0] < 0 ? -1 : (double)(perf_read(0) - i0), perf_fd[1] < 0 ? -1 : (double)(perf_read(1) - c0), r.engine_ms };
          nt++;
        }
        B->finalize(st);
      } else {
        double t0 = now_ms();
        if (prep(q, &st, &r0)) goto bad;
        exec1(q, st, &r0);
        B->finalize(st);
        first = now_ms() - t0;
        perf_reset();
        for (int k = 0; k < reps && (k < minr || spent < budget) && !r0.err[0]; k++) {
          long long i0 = ex ? perf_read(0) : 0, c0 = ex ? perf_read(1) : 0;
          perf_on(1);
          t0 = now_ms();
          prep(q, &st, &r);
          double tp = now_ms() - t0;
          perf_on(0);
          exec1(q, st, &r); /* (its BEGIN/ROLLBACK for a write untimed, as in hot) */
          perf_on(1);
          t0 = now_ms();
          B->finalize(st);
          t[nt] = tp + t_exec + (now_ms() - t0);
          perf_on(0);
          if (ex)
            ex[nt] =
              (struct E){ r.alt, t[nt], perf_fd[0] < 0 ? -1 : (double)(perf_read(0) - i0), perf_fd[1] < 0 ? -1 : (double)(perf_read(1) - c0), r.engine_ms };
          spent += t[nt++];
          alt += r.alt;
        }
      }
      {
        double sum = 0;
        for (int k = 0; k < nt; k++) sum += t[k];
        long long ins = perf_read(0), cyc = perf_read(1);
#ifdef SG_CALLGRIND
        {
          char lb[300];
          snprintf(lb, sizeof lb, "%s %s %d", q->id, hot ? "hot" : "prep", nt);
          CALLGRIND_DUMP_STATS_AT(lb);
        }
#endif
        qsort(t, (size_t)nt, sizeof *t, cmpd);
        clean(r0.err);
        printf("T\t%s\t%ld\t%016llx\t%.4f\t%.4f\t%.5f\t%d\t%.1f\t%.1f\t%d/%d\t%s\n", q->id, r0.rows, r0.h, first, nt ? t[nt / 2] : first, nt ? sum / nt : first,
               nt, ins >= 0 && nt ? (double)ins / nt : -1.0, cyc >= 0 && nt ? (double)cyc / nt : -1.0, alt, nt, r0.err[0] ? r0.err : "-");
        for (int k = 0; ex && k < nt; k++)
          printf("E\t%s\t%d\t%d\t%.5f\t%.0f\t%.0f\t%.5f\n", q->id, k + 1, ex[k].alt, ex[k].ms, ex[k].ins, ex[k].cyc, ex[k].eng);
      }
      fflush(stdout);
      continue;
    bad:
      clean(r0.err);
      printf("T\t%s\t0\t0\t0\t0\t0\t0\t-1\t-1\t0/0\t%s\n", q->id, r0.err);
      continue;
    }
    fflush(stdout);
  }
  if (s_begin) B->finalize(s_begin);
  if (s_rollback) B->finalize(s_rollback);
  B->close(db);
  return 0;
}
