/* backends/postgres/adapter.c: the backend over libpq, a client adapter for a PostgreSQL server.
** - Server: SG_PG_URL (default postgresql://postgres@127.0.0.1:5432; the backend's arm defaults set it, make postgres).
** - Databases: the driver works on database files, so the "file" here is a marker holding a database's name. Opening a
**   path whose marker is missing or empty creates a fresh database, sg_<file name>, and writes the marker; the
**   runner's copies of the marker then all name that database. Writes run inside BEGIN ... ROLLBACK, as for every
**   backend, so copies can share it.
** - Statements: server-side prepared statements (PQprepare, PQexecPrepared), parameters $1..$N (the dialect layer
**   numbers them), every parameter sent as text with its type left to the server; results in text format, fully
**   fetched by the first step.
** - Values: int2/int4/int8 and oid as integers; float4/float8 as floats; numeric as an integer when it has no fraction
**   and fits, else a float; bool as 1/0; bytea as a blob; everything else (text, varchar, dates, json, ...) as text.
** - Not yet: engine_ms (the server's own execution time) is -1. */
#include <ctype.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "libpq-fe.h"
#include "sqlgauntlet.h"

typedef struct {
  PGconn *c;
  char err[512];
  int64_t changes;
  unsigned long nstmt;
} PC;
typedef struct {
  PC *c;
  char name[32];
  int np, nc, row, nrows;
  char **pv; /* the bound parameters, as text (NULL: SQL NULL) */
  PGresult *res;
  unsigned char *blob; /* the current bytea column, decoded */
  size_t bloblen;
} PS;

static void seterr(PC *c, const char *e, char *out, size_t n) {
  snprintf(c->err, sizeof c->err, "%s", e ? e : "postgres error");
  for (char *p = c->err; *p; p++)
    if (*p == '\n') *p = ' ';
  if (out) snprintf(out, n, "%s", c->err);
}
static const char *url(void) {
  const char *u = getenv("SG_PG_URL");
  return u && *u ? u : "postgresql://postgres@127.0.0.1:5432";
}
static PGconn *connect_db(const char *db, char *err, size_t n) { /* the server's URL with another database */
  char conninfo[1024];
  snprintf(conninfo, sizeof conninfo, "%s dbname=%s", url(), db);
  const char *kw[] = { "dbname", "dbname", 0 }, *val[] = { url(), db, 0 }; /* (a URI first, then the database over it) */
  PGconn *c = PQconnectdbParams(kw, val, 1);
  if (PQstatus(c) != CONNECTION_OK) {
    snprintf(err, n, "connect %s: %s", db, PQerrorMessage(c));
    PQfinish(c);
    return 0;
  }
  PQclear(PQexec(c, "SET client_min_messages = warning"));
  return c;
}
static sg_conn *p_open(const char *path, char *err, size_t n) {
  char db[128] = "";
  FILE *f = fopen(path, "r");
  if (f) {
    if (!fgets(db, sizeof db, f)) db[0] = 0;
    fclose(f);
    db[strcspn(db, "\r\n")] = 0;
  }
  if (!db[0]) { /* a fresh database, named after the file */
    const char *b = strrchr(path, '/');
    b = b ? b + 1 : path;
    size_t k = (size_t)snprintf(db, sizeof db, "sg_");
    for (; *b && *b != '.' && k < sizeof db - 1; b++) db[k++] = isalnum((unsigned char)*b) ? (char)tolower((unsigned char)*b) : '_';
    db[k] = 0;
    PGconn *m = connect_db("postgres", err, n);
    if (!m) return 0;
    char sql[300];
    snprintf(sql, sizeof sql, "DROP DATABASE IF EXISTS %s WITH (FORCE)", db);
    PQclear(PQexec(m, sql));
    snprintf(sql, sizeof sql, "CREATE DATABASE %s", db);
    PGresult *r = PQexec(m, sql);
    int ok = PQresultStatus(r) == PGRES_COMMAND_OK;
    if (!ok) snprintf(err, n, "%s", PQerrorMessage(m));
    PQclear(r);
    PQfinish(m);
    if (!ok) return 0;
    if (!(f = fopen(path, "w"))) {
      snprintf(err, n, "cannot write %s", path);
      return 0;
    }
    fprintf(f, "%s\n", db);
    fclose(f);
  }
  PGconn *c = connect_db(db, err, n);
  if (!c) return 0;
  PC *pc = calloc(1, sizeof *pc);
  pc->c = c;
  return (sg_conn *)pc;
}
static void p_close(sg_conn *x) {
  PC *c = (PC *)x;
  PQfinish(c->c);
  free(c);
}
static int p_exec(sg_conn *x, const char *sql, char *err, size_t n) {
  PC *c = (PC *)x;
  PGresult *r = PQexec(c->c, sql);
  ExecStatusType s = PQresultStatus(r);
  int bad = s != PGRES_COMMAND_OK && s != PGRES_TUPLES_OK && s != PGRES_EMPTY_QUERY;
  if (bad) seterr(c, PQresultErrorMessage(r), err, n);
  PQclear(r);
  return bad;
}
static sg_stmt *p_prepare(sg_conn *x, const char *sql, char *err, size_t n) {
  PC *c = (PC *)x;
  PS *s = calloc(1, sizeof *s);
  s->c = c;
  snprintf(s->name, sizeof s->name, "sg%lu", ++c->nstmt);
  PGresult *r = PQprepare(c->c, s->name, sql, 0, 0);
  if (PQresultStatus(r) != PGRES_COMMAND_OK) {
    seterr(c, PQresultErrorMessage(r), err, n);
    PQclear(r);
    free(s);
    return 0;
  }
  PQclear(r);
  r = PQdescribePrepared(c->c, s->name);
  s->np = PQnparams(r);
  s->nc = PQnfields(r);
  PQclear(r);
  s->pv = calloc((size_t)s->np + 1, sizeof *s->pv);
  return (sg_stmt *)s;
}
static int p_param_count(sg_stmt *x) { return ((PS *)x)->np; }
static int p_param_index(sg_stmt *x, const char *name) { /* "?N" and "$N" (the dialect layer numbers them) */
  int k = (name[0] == '?' || name[0] == '$') ? atoi(name + 1) : 0;
  return k >= 1 && k <= ((PS *)x)->np ? k : 0;
}
static int p_bind(sg_stmt *x, int i, const sg_value *v) {
  PS *s = (PS *)x;
  if (i < 1 || i > s->np) return SG_ERROR;
  free(s->pv[i - 1]);
  s->pv[i - 1] = 0;
  char b[64];
  switch (v->type) {
    case SG_INTEGER:
      snprintf(b, sizeof b, "%lld", (long long)v->i);
      s->pv[i - 1] = strdup(b);
      break;
    case SG_FLOAT:
      snprintf(b, sizeof b, "%.17g", v->f);
      s->pv[i - 1] = strdup(b);
      break;
    case SG_TEXT: s->pv[i - 1] = strndup(v->p, v->n); break;
    case SG_BLOB: {
      char *h = malloc(v->n * 2 + 3);
      memcpy(h, "\\x", 2);
      for (size_t k = 0; k < v->n; k++) snprintf(h + 2 + 2 * k, 3, "%02x", ((const unsigned char *)v->p)[k]);
      s->pv[i - 1] = h;
      break;
    }
    default: break; /* NULL */
  }
  return SG_OK;
}
static int p_step(sg_stmt *x) {
  PS *s = (PS *)x;
  if (!s->res) {
    s->res = PQexecPrepared(s->c->c, s->name, s->np, (const char *const *)s->pv, 0, 0, 0);
    ExecStatusType st = PQresultStatus(s->res);
    if (st != PGRES_TUPLES_OK && st != PGRES_COMMAND_OK) {
      seterr(s->c, PQresultErrorMessage(s->res), 0, 0);
      return SG_ERROR;
    }
    s->nrows = PQntuples(s->res);
    s->row = -1;
    const char *ct = PQcmdTuples(s->res);
    s->c->changes = st == PGRES_COMMAND_OK || (*ct && strncmp(PQcmdStatus(s->res), "SELECT", 6)) ? atoll(ct) : 0;
  }
  if (PQresultStatus(s->res) != PGRES_TUPLES_OK && PQresultStatus(s->res) != PGRES_COMMAND_OK) return SG_ERROR;
  return ++s->row < s->nrows ? SG_ROW : SG_DONE;
}
static int p_column_count(sg_stmt *x) { return ((PS *)x)->nc; }
static void p_column(sg_stmt *x, int i, sg_value *v) {
  PS *s = (PS *)x;
  memset(v, 0, sizeof *v);
  if (!s->res || s->row < 0 || s->row >= s->nrows || PQgetisnull(s->res, s->row, i)) {
    v->type = SG_NULL;
    return;
  }
  const char *t = PQgetvalue(s->res, s->row, i);
  size_t len = (size_t)PQgetlength(s->res, s->row, i);
  char *e;
  switch (PQftype(s->res, i)) {
    case 20:
    case 21:
    case 23:
    case 26:
      v->type = SG_INTEGER;
      v->i = strtoll(t, 0, 10);
      return; /* int8, int2, int4, oid */
    case 700:
    case 701:
      v->type = SG_FLOAT;
      v->f = strtod(t, 0);
      return; /* float4, float8 */
    case 16:
      v->type = SG_INTEGER;
      v->i = t[0] == 't';
      return;  /* bool */
    case 1700: /* numeric */
      if (!strpbrk(t, ".eEN") && len < 19) {
        v->type = SG_INTEGER;
        v->i = strtoll(t, &e, 10);
      } else {
        v->type = SG_FLOAT;
        v->f = strtod(t, 0);
      }
      return;
    case 17: /* bytea, hex output */
      free(s->blob);
      s->blob = PQunescapeBytea((const unsigned char *)t, &s->bloblen);
      v->type = SG_BLOB;
      v->p = s->blob;
      v->n = s->bloblen;
      return;
    default:
      v->type = SG_TEXT;
      v->p = t;
      v->n = len;
  }
}
static int p_reset(sg_stmt *x) {
  PS *s = (PS *)x;
  PQclear(s->res);
  s->res = 0;
  return SG_OK;
}
static void p_finalize(sg_stmt *x) {
  PS *s = (PS *)x;
  PQclear(s->res);
  char sql[64];
  snprintf(sql, sizeof sql, "DEALLOCATE %s", s->name);
  PQclear(PQexec(s->c->c, sql));
  for (int i = 0; i < s->np; i++) free(s->pv[i]);
  free(s->pv);
  if (s->blob) PQfreemem(s->blob);
  free(s);
}
static int64_t p_changes(sg_conn *x) { return ((PC *)x)->changes; }
static const char *p_errmsg(sg_conn *x) { return ((PC *)x)->err; }
static const char *p_version(void) {
  static char b[160];
  char err[256];
  PGconn *c = connect_db("postgres", err, sizeof err);
  if (!c) return "PostgreSQL (server unreachable)";
  PGresult *r = PQexec(c, "SHOW server_version");
  snprintf(b, sizeof b, "PostgreSQL %s (libpq %d)", PQresultStatus(r) == PGRES_TUPLES_OK ? PQgetvalue(r, 0, 0) : "?", PQlibVersion());
  PQclear(r);
  PQfinish(c);
  return b;
}

static const sg_backend backend = {
  SQLGAUNTLET_ABI,
  "postgres",
  "postgres",
  p_version,
  p_open,
  p_close,
  p_exec,
  p_prepare,
  p_param_count,
  p_param_index,
  p_bind,
  p_step,
  p_column_count,
  p_column,
  p_reset,
  p_finalize,
  p_changes,
  p_errmsg,
  0,
  0,
  0,
  0,
};
__attribute__((visibility("default"))) const sg_backend *sqlgauntlet_backend_v1(void) { return &backend; }
